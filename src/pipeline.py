"""End-to-end orchestration: URL/file -> analysis -> ranked clips -> renders -> metadata -> schedule."""
from __future__ import annotations

import json

from . import db
from .common import DIRS, fmt_ts, get_logger, profile
from .detection import candidates, signals
from .editing.render import render
from .ingestion import rights
from .ingestion.download import ingest
from .metadata.generate import generate
from .publishing.scheduler import schedule_all
from .scoring.review import review
from .transcription.transcribe import transcribe

log = get_logger("pipeline")


def process(url: str, streamer: str | None = None, game: str | None = None,
            rights_status: str | None = None, commercial: bool = False, auth_ref: str | None = None,
            authorized_on: str | None = None, restrictions: str | None = None,
            max_clips: int | None = None, schedule: bool = True, force_top: int = 0) -> dict:
    pr = profile()
    log.info("=== Profile: %s (%s)", pr["id"], pr["name"])
    src = ingest(url, streamer=streamer, game=game)
    if rights_status:
        src = rights.set_rights(src["id"], rights_status, commercial, authorized_on, auth_ref,
                                restrictions, streamer, game)
    ok, why = rights.publish_allowed(src)
    if not ok:
        log.warning("PUBLISHING BLOCKED for this source: %s. Clips will be rendered but not scheduled.", why)

    tr = transcribe(src)
    sig = signals.extract(src, tr)
    src = db.one("SELECT * FROM sources WHERE id=?", (src["id"],))  # refresh layout_json
    cands = candidates.find(tr, sig, src["duration"])
    kept = review(src, tr, sig, cands)
    if max_clips:
        for k in kept[max_clips:]:
            db.execute("UPDATE candidates SET status='rejected', reject_reason='over --max-clips' WHERE id=?",
                       (k["db_id"],))
        kept = kept[:max_clips]
    preview_ids = set()
    if force_top and len(kept) < force_top:
        # preview renders of the best rejected moments: never scheduled, flagged in DB
        for r in db.rows("SELECT * FROM candidates WHERE source_id=? AND status='rejected' "
                         "ORDER BY virality DESC LIMIT ?", (src["id"], force_top - len(kept))):
            rj = json.loads(r["review_json"] or "{}")
            kept.append({**r, "db_id": r["id"], "on_screen_hook": rj.get("on_screen_hook", "")})
            preview_ids.add(r["id"])
        log.warning("--force-top: rendering %d rejected moment(s) as PREVIEW (never scheduled)", len(preview_ids))

    _write_candidate_report(src, cands_all=db.rows(
        "SELECT * FROM candidates WHERE source_id=? AND status<>'superseded' ORDER BY virality DESC", (src["id"],)))

    made = []
    for rank, c in enumerate(kept, 1):
        is_preview = c["db_id"] in preview_ids
        clip_id = f"{src['id']}_{rank:02d}" + ("_preview" if is_preview else "")
        # never overwrite an existing clip on re-runs: it may carry human edits, approval or uploads.
        # (use `main.py rerender <clip_id>` / the dashboard to deliberately re-render one)
        overlap = db.one("SELECT id FROM clips WHERE source_id=? AND source_end>? AND source_start<? "
                         "AND COALESCE(preview,0)=? LIMIT 1", (src["id"], c["start"] + 2, c["end"] - 2, int(is_preview)))
        if db.one("SELECT id FROM clips WHERE id=?", (clip_id,)) or overlap:
            log.info("Clip for this moment already exists (%s) - keeping it untouched", (overlap or {}).get("id", clip_id))
            continue
        log.info("--- Clip %d/%d  [%s-%s] score=%.1f %s", rank, len(kept), fmt_ts(c["start"]), fmt_ts(c["end"]),
                 c["virality"], c["category"])
        try:
            meta = generate(src, tr, c, clip_id)
            hook = meta.get("on_screen_hook") or c.get("on_screen_hook") or ""
            res = render(src, tr, sig, c, clip_id, corrections=meta.get("caption_corrections"), hook_text=hook)
        except Exception as e:
            log.error("Clip %s failed: %s", clip_id, e)
            continue
        meta["render"] = {k: res[k] for k in ("layout", "dead_air_removed_s", "punch_ins")}
        (DIRS["metadata"] / f"{clip_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                                         encoding="utf-8")
        db.execute("INSERT OR REPLACE INTO clips (id,source_id,candidate_id,path,sha256,duration,layout,category,"
                   "virality,source_start,source_end,metadata_json,commentary_json,created_at,preview) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (clip_id, src["id"], c["db_id"], res["path"], res["sha256"], res["duration"], res["layout"],
                    c["category"], c["virality"], c["start"], c["end"], db.dumps(meta),
                    db.dumps(meta.get("commentary") or {}), db.now_iso(), int(is_preview)))
        if not is_preview:
            db.execute("UPDATE candidates SET status='rendered' WHERE id=?", (c["db_id"],))
        made.append({"clip_id": clip_id, "path": res["path"], "score": c["virality"],
                     "title": meta["youtube"]["best_title"], "preview": is_preview})

    sched = schedule_all() if (schedule and ok) else {"scheduled": 0}
    summary = {"source_id": src["id"], "profile": pr["id"], "candidates": len(cands), "selected": len(kept),
               "rendered": len(made), "scheduled": sched.get("scheduled", 0),
               "publishing_allowed": ok, "publishing_note": why, "clips": made}
    (DIRS["final_clips"] / f"{src['id']}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _write_candidate_report(src: dict, cands_all: list[dict]) -> None:
    lines = [f"# Candidate moments — {src.get('title') or src['id']}",
             f"Profile: {src.get('profile')} | Streamer: {src.get('streamer')} | Rights: {src.get('rights_status')}"
             f" | commercial: {bool(src.get('commercial_use'))}", "",
             "| rank | status | score | time | category | what happened | why / reject reason |",
             "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(cands_all, 1):
        lines.append(f"| {i} | {c['status']} | {c['virality']:.0f} | {fmt_ts(c['start'])}–{fmt_ts(c['end'])} | "
                     f"{c['category']} | {(c['summary'] or '').replace('|', '/')} | "
                     f"{(c['reject_reason'] or c['why'] or '').replace('|', '/')} |")
    (DIRS["candidate_clips"] / f"{src['id']}.md").write_text("\n".join(lines), encoding="utf-8")
    (DIRS["candidate_clips"] / f"{src['id']}.json").write_text(json.dumps(cands_all, indent=2, default=str),
                                                               encoding="utf-8")
