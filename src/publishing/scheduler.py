"""Slot scheduling for Western time zones + the publish-due worker (run every ~15 min)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import db
from ..common import DIRS, cfg, get_logger
from ..ingestion.rights import publish_allowed, restriction_blocks

log = get_logger("scheduler")
PLATFORMS = ("youtube", "instagram")
ACTIVE = ("scheduled", "uploading", "uploaded", "published")


def _tz():
    return ZoneInfo(cfg()["publishing"]["timezone"])


def _slots(platform: str, days: int = 60):
    pc = cfg()["publishing"][platform]
    tz = _tz()
    today = datetime.now(tz).date()
    for d in range(days):
        day = today + timedelta(days=d)
        for s in pc["slots"]:
            hh, mm = map(int, s.split(":"))
            yield datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)


def schedule_all() -> dict:
    p = cfg()["publishing"]
    summary = {"scheduled": 0, "blocked": []}
    clips = db.rows("SELECT c.*, s.rights_status, s.commercial_use, s.restrictions, s.streamer "
                    "FROM clips c JOIN sources s ON s.id=c.source_id WHERE COALESCE(c.preview,0)=0 "
                    + ("AND c.review_status='approved' " if p.get("require_approval", True) else
                       "AND COALESCE(c.review_status,'pending')<>'rejected' ")
                    + "ORDER BY c.virality DESC")
    for plat in PLATFORMS:
        if not p[plat]["enabled"]:
            continue
        taken = [datetime.fromisoformat(r["scheduled_for"]) for r in db.rows(
            "SELECT scheduled_for FROM publications WHERE platform=? AND status IN ('scheduled','uploading','uploaded','published')",
            (plat,)) if r["scheduled_for"]]
        last_cat = {r["clip_id"]: r for r in db.rows(
            "SELECT p.clip_id, p.scheduled_for, c.category FROM publications p JOIN clips c ON c.id=p.clip_id "
            "WHERE p.platform=?", (plat,))}
        pending = []
        for c in clips:
            if db.one("SELECT id FROM publications WHERE clip_id=? AND platform=?", (c["id"], plat)):
                continue
            ok, why = publish_allowed(c)
            block = restriction_blocks(c, plat)
            if not ok or block:
                summary["blocked"].append(f"{c['id']} [{plat}]: {why if not ok else block}")
                continue
            dup = db.one("SELECT p.id FROM publications p JOIN clips c2 ON c2.id=p.clip_id "
                         "WHERE c2.sha256=? AND p.platform=? AND p.status IN ('scheduled','uploading','uploaded','published')",
                         (c["sha256"], plat))
            if dup:
                continue
            pending.append(c)
        cat_by_time = sorted(((datetime.fromisoformat(v["scheduled_for"]), v["category"])
                              for v in last_cat.values() if v["scheduled_for"]))
        prev_cat = cat_by_time[-1][1] if cat_by_time else None
        now = datetime.now(timezone.utc) + timedelta(minutes=20)
        gap = timedelta(hours=p["min_gap_hours"])
        for slot in _slots(plat):
            if not pending:
                break
            if slot < now:
                continue
            day_local = slot.astimezone(_tz()).date()
            same_day = sum(1 for t in taken if t.astimezone(_tz()).date() == day_local)
            if same_day >= p[plat]["daily_limit"]:
                continue
            if any(abs(t - slot) < gap for t in taken):
                continue
            # prefer the best clip whose category differs from the previous slot (avoid repetition)
            pick = next((c for c in pending if c["category"] != prev_cat), pending[0])
            pending.remove(pick)
            db.execute("INSERT INTO publications (clip_id,platform,status,scheduled_for) VALUES (?,?,?,?)",
                       (pick["id"], plat, "scheduled", slot.isoformat()))
            taken.append(slot)
            prev_cat = pick["category"]
            summary["scheduled"] += 1
            log.info("Scheduled %s on %s at %s ET", pick["id"], plat,
                     slot.astimezone(_tz()).strftime("%a %b %d %H:%M"))
    for b in summary["blocked"][:10]:
        log.warning("Not scheduled - %s", b)
    return summary


def _lock():
    lock = DIRS["work"] / "publish.lock"
    if lock.exists() and (datetime.now().timestamp() - lock.stat().st_mtime) < 3 * 3600:
        return None
    lock.write_text(str(os.getpid()))
    return lock


def publish_due() -> dict:
    lock = _lock()
    if not lock:
        log.info("Another publish worker is running - skipping")
        return {}
    res = {"youtube": 0, "instagram": 0, "errors": 0}
    try:
        now = datetime.now(timezone.utc)
        p = cfg()["publishing"]
        for row in db.rows("SELECT p.*, c.path, c.metadata_json, c.source_id FROM publications p "
                           "JOIN clips c ON c.id=p.clip_id WHERE p.status='scheduled' ORDER BY p.scheduled_for"):
            when = datetime.fromisoformat(row["scheduled_for"])
            plat = row["platform"]
            if plat == "youtube":
                if when - timedelta(hours=p["youtube"]["upload_lead_hours"]) > now:
                    continue
                used = db.one("SELECT COUNT(*) n FROM publications WHERE platform='youtube' AND uploaded_at>=?",
                              ((now - timedelta(hours=24)).isoformat(),))["n"]
                if used >= p["youtube"]["quota_uploads_per_day"]:
                    log.info("YouTube daily quota budget reached - deferring")
                    continue
            elif when > now:
                continue
            src = db.one("SELECT * FROM sources WHERE id=?", (row["source_id"],))
            ok, why = publish_allowed(src)
            block = restriction_blocks(src, plat)
            if not ok or block:
                db.execute("UPDATE publications SET status='blocked', last_error=? WHERE id=?",
                           (why if not ok else block, row["id"]))
                continue
            meta = json.loads(row["metadata_json"])
            db.execute("UPDATE publications SET status='uploading', attempts=attempts+1 WHERE id=?", (row["id"],))
            try:
                if plat == "youtube":
                    from . import youtube
                    future = when > now + timedelta(minutes=15)
                    r = youtube.upload(row["path"], meta, privacy="private" if future else "public",
                                       publish_at_utc=when.isoformat() if future else None)
                    db.execute("UPDATE publications SET status=?, remote_id=?, url=?, uploaded_at=?, published_at=? "
                               "WHERE id=?", ("uploaded" if future else "published", r["id"], r["url"],
                                              db.now_iso(), when.isoformat() if future else db.now_iso(), row["id"]))
                else:
                    from . import instagram
                    r = instagram.publish(row["path"], meta["instagram"]["caption_full"])
                    db.execute("UPDATE publications SET status='published', remote_id=?, url=?, uploaded_at=?, "
                               "published_at=? WHERE id=?", (r["id"], r["url"], db.now_iso(), db.now_iso(), row["id"]))
                res[plat] += 1
            except Exception as e:
                res["errors"] += 1
                att = db.one("SELECT attempts FROM publications WHERE id=?", (row["id"],))["attempts"]
                status = "failed" if att >= 3 else "scheduled"
                db.execute("UPDATE publications SET status=?, last_error=? WHERE id=?",
                           (status, str(e)[:1000], row["id"]))
                log.error("Publish %s/%s failed (attempt %d): %s", row["clip_id"], plat, att, e)
        # YouTube scheduled uploads become 'published' once their publishAt time passes
        db.execute("UPDATE publications SET status='published' WHERE platform='youtube' AND status='uploaded' "
                   "AND published_at<=?", (db.now_iso(),))
    finally:
        lock.unlink(missing_ok=True)
    return res
