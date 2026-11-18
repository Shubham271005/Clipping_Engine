"""Clip Studio - web dashboard for the AI clipping engine.

Run:  .venv\\Scripts\\python -m web.server            (http://127.0.0.1:8765, no login on localhost)
      .venv\\Scripts\\python -m web.server --host 0.0.0.0   (requires DASHBOARD_PASSWORD in .env)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import statistics
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src import common, db
from src.common import DIRS, PROFILES_DIR, ROOT, cfg, get_logger, list_profiles, load_profile
from .jobs import JobManager

log = get_logger("web")
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Clip Studio", docs_url=None, redoc_url=None)
jobs: JobManager | None = None
SESSIONS: set[str] = set()
AUTH = {"required": False}
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


# ===================================================================== auth
def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    open_paths = ("/login.html", "/api/login", "/api/session", "/assets/", "/css/", "/js/")
    if AUTH["required"] and not path.startswith(open_paths):
        tok = request.cookies.get("cs_session")
        if tok not in SESSIONS:
            if path.startswith(("/api/", "/media/")):
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            return RedirectResponse("/login.html")
    return await call_next(request)


@app.post("/api/login")
def login(body: dict = Body(...)):
    if not AUTH["required"]:
        return {"ok": True}
    if not hmac.compare_digest(str(body.get("password", "")), PASSWORD):
        time.sleep(1)  # slow down brute force
        raise HTTPException(401, "wrong password")
    tok = secrets.token_urlsafe(32)
    SESSIONS.add(tok)
    r = JSONResponse({"ok": True})
    r.set_cookie("cs_session", tok, httponly=True, samesite="strict", max_age=7 * 86400)
    return r


@app.post("/api/logout")
def logout(request: Request):
    SESSIONS.discard(request.cookies.get("cs_session", ""))
    r = JSONResponse({"ok": True})
    r.delete_cookie("cs_session")
    return r


@app.get("/api/session")
def session(request: Request):
    return {"auth_required": AUTH["required"],
            "authenticated": (not AUTH["required"]) or request.cookies.get("cs_session") in SESSIONS}


# ===================================================================== helpers
def _err(e: Exception):
    raise HTTPException(400, str(e))


def _clip_or_404(clip_id: str) -> dict:
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not c:
        raise HTTPException(404, "clip not found")
    return c


def _clip_summary(c: dict) -> dict:
    meta = json.loads(c["metadata_json"] or "{}")
    return {k: c.get(k) for k in ("id", "source_id", "candidate_id", "duration", "layout", "category", "virality",
                                  "source_start", "source_end", "preview", "review_status", "narrated",
                                  "created_at", "updated_at")} | {
        "title": (meta.get("youtube") or {}).get("best_title"), "hook": meta.get("on_screen_hook"),
        "file_exists": bool(c["path"] and Path(c["path"]).exists()),
        "publications": db.rows("SELECT id,platform,status,scheduled_for,url,last_error FROM publications "
                                "WHERE clip_id=?", (c["id"],))}


def _reset_cfg():
    common._CFG = None
    common._PROFILE = None


# ===================================================================== overview
@app.get("/api/overview")
def overview():
    q = lambda sql, a=(): db.one(sql, a)["n"]  # noqa: E731
    now = db.now_iso()
    return {
        "sources": q("SELECT COUNT(*) n FROM sources"),
        "clips": {s: q("SELECT COUNT(*) n FROM clips WHERE COALESCE(review_status,'pending')=? AND COALESCE(preview,0)=0", (s,))
                  for s in ("pending", "approved", "rejected")},
        "previews": q("SELECT COUNT(*) n FROM clips WHERE preview=1"),
        "scheduled": q("SELECT COUNT(*) n FROM publications WHERE status IN ('scheduled','uploaded') AND scheduled_for>=?", (now,)),
        "published": q("SELECT COUNT(*) n FROM publications WHERE status='published'"),
        "failed": q("SELECT COUNT(*) n FROM publications WHERE status IN ('failed','blocked')"),
        "jobs_active": q("SELECT COUNT(*) n FROM jobs WHERE status IN ('running','paused','queued')"),
        "next_up": db.rows("SELECT p.id,p.clip_id,p.platform,p.scheduled_for,c.metadata_json FROM publications p "
                           "JOIN clips c ON c.id=p.clip_id WHERE p.status IN ('scheduled','uploaded') AND p.scheduled_for>=? "
                           "ORDER BY p.scheduled_for LIMIT 5", (now,)),
        "recent_jobs": db.rows("SELECT id,kind,label,status,progress,stage,created_at FROM jobs ORDER BY id DESC LIMIT 6"),
        "review_queue": [_clip_summary(c) for c in db.rows(
            "SELECT * FROM clips WHERE COALESCE(review_status,'pending')='pending' AND COALESCE(preview,0)=0 "
            "ORDER BY virality DESC LIMIT 6")],
        "connections": connections(),
        "auto_publish": bool(_state().get("auto_publish")),
    }


# ===================================================================== profiles & settings
@app.get("/api/profiles")
def profiles():
    out = []
    for name in list_profiles():
        try:
            p = load_profile(name)
            out.append({"id": name, "name": p["name"], "game": p.get("game"), "content_type": p["content_type"],
                        "categories": len(p["categories"]), "max_clip_s": p["detection"].get("max_clip_s")})
        except Exception as e:
            out.append({"id": name, "name": name, "error": str(e)})
    return out


@app.get("/api/profiles/{name}")
def profile_get(name: str):
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, "profile not found")
    return {"id": name, "yaml": path.read_text(encoding="utf-8"), "resolved": load_profile(name),
            "base_yaml": (PROFILES_DIR / "_base.yaml").read_text(encoding="utf-8")}


def _valid_name(name: str) -> str:
    if not name or not all(ch.isalnum() or ch in "-_" for ch in name) or name.startswith("_"):
        raise HTTPException(400, "profile id may only contain letters, digits, - and _")
    return name.lower()


@app.put("/api/profiles/{name}")
def profile_put(name: str, body: dict = Body(...)):
    name = _valid_name(name)
    text = body.get("yaml", "")
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("profile must be a YAML mapping")
    except Exception as e:
        raise HTTPException(400, f"Invalid YAML: {e}")
    path = PROFILES_DIR / f"{name}.yaml"
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(text, encoding="utf-8")
    try:
        p = load_profile(name)
        w = p["scoring"]["weights"]
        assert p["categories"] and "other" in p["categories"], "categories must include 'other'"
        assert abs(sum(w.values())) > 0, "scoring weights must not be empty"
        for k in ("loudness", "hype_words", "laughter", "speech_rate", "visual_change", "flash", "pause_then_burst"):
            float(p["detection"]["signal_weights"][k])
        import re as _re
        _re.compile("|".join(p["detection"].get("hype_terms", []) + p["detection"].get("extra_hype_terms", [])))
    except Exception as e:
        if backup is None:
            path.unlink()
        else:
            path.write_text(backup, encoding="utf-8")
        raise HTTPException(400, f"Profile rejected: {e}")
    _reset_cfg()
    return {"ok": True, "id": name}


@app.post("/api/profiles")
def profile_create(body: dict = Body(...)):
    name = _valid_name(body.get("id", ""))
    if (PROFILES_DIR / f"{name}.yaml").exists():
        raise HTTPException(400, "profile already exists")
    src = PROFILES_DIR / f"{_valid_name(body.get('copy_from') or 'gta6')}.yaml"
    if not src.exists():
        raise HTTPException(400, "source profile not found")
    text = src.read_text(encoding="utf-8")
    if body.get("display_name"):
        lines = text.splitlines()
        lines = [f"name: {body['display_name']}" if l.startswith("name:") else l for l in lines]
        text = "\n".join(lines) + "\n"
    return profile_put(name, {"yaml": text})


@app.delete("/api/profiles/{name}")
def profile_delete(name: str):
    name = _valid_name(name)
    used = db.one("SELECT COUNT(*) n FROM sources WHERE profile=?", (name,))["n"]
    if used:
        raise HTTPException(400, f"profile is used by {used} source(s); it cannot be deleted")
    if len(list_profiles()) <= 1:
        raise HTTPException(400, "cannot delete the last profile")
    (PROFILES_DIR / f"{name}.yaml").unlink(missing_ok=True)
    return {"ok": True}


SETTINGS = ROOT / "config" / "settings.yaml"


@app.get("/api/settings")
def settings_get():
    return {"yaml": SETTINGS.read_text(encoding="utf-8"), "resolved": yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))}


@app.put("/api/settings")
def settings_put(body: dict = Body(...)):
    text = body.get("yaml")
    if text is None:  # structured patch (dot paths) -> applied to YAML
        data = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
        for path, val in (body.get("patch") or {}).items():
            node = data
            keys = path.split(".")
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = val
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    try:
        data = yaml.safe_load(text)
        for sec in ("transcription", "detection", "review", "editing", "publishing"):
            assert isinstance(data.get(sec), dict), f"missing section '{sec}'"
        for plat in ("youtube", "instagram"):
            pc = data["publishing"][plat]
            for s in pc["slots"]:
                hh, mm = map(int, str(s).split(":"))
                assert 0 <= hh < 24 and 0 <= mm < 60, f"bad slot {s}"
            assert int(pc["daily_limit"]) >= 0
        ZoneInfo(data["publishing"]["timezone"])
    except Exception as e:
        raise HTTPException(400, f"Settings rejected: {e}")
    shutil.copy2(SETTINGS, DIRS["work"] / "settings.backup.yaml")
    SETTINGS.write_text(text, encoding="utf-8")
    _reset_cfg()
    return {"ok": True}


# ===================================================================== jobs
@app.get("/api/jobs")
def jobs_list(limit: int = 50):
    return db.rows("SELECT id,kind,label,status,progress,stage,source_id,created_at,started_at,finished_at,result_json "
                   "FROM jobs ORDER BY id DESC LIMIT ?", (limit,))


@app.post("/api/jobs")
def jobs_create(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL or file path is required")
    prof = body.get("profile") or "gta6"
    if prof not in list_profiles():
        raise HTTPException(400, f"unknown profile {prof}")
    if body.get("rights") == "permission_granted" and not body.get("auth_ref"):
        raise HTTPException(400, "permission_granted needs a reference to where the permission is stored")
    args = {k: body.get(k) for k in ("url", "profile", "streamer", "game", "rights", "commercial", "auth_ref",
                                     "authorized_on", "restrictions", "max_clips", "min_score", "force_top", "no_llm")}
    args["url"], args["profile"] = url, prof
    label = f"[{prof}] {body.get('streamer') or ''} {url}".strip()
    return {"id": jobs.submit("process", args, label[:200])}


@app.get("/api/jobs/{jid}")
def job_get(jid: int):
    j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j:
        raise HTTPException(404, "job not found")
    j["args"] = json.loads(j.pop("args_json") or "{}")
    j["result"] = json.loads(j.pop("result_json") or "null")
    return j


@app.get("/api/jobs/{jid}/log")
def job_log(jid: int, offset: int = 0):
    return jobs.read_log(jid, offset)


@app.post("/api/jobs/{jid}/{action}")
def job_action(jid: int, action: str):
    try:
        if action == "pause":
            jobs.pause(jid)
        elif action == "resume":
            jobs.resume(jid)
        elif action == "cancel":
            jobs.cancel(jid)
        elif action == "retry":
            j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
            if not j or j["status"] not in ("failed", "cancelled", "interrupted"):
                raise ValueError("only failed, cancelled or interrupted jobs can be retried")
            return {"id": jobs.submit(j["kind"], json.loads(j["args_json"]), j["label"])}
        else:
            raise HTTPException(404, "unknown action")
    except ValueError as e:
        _err(e)
    return {"ok": True}


# ===================================================================== sources / candidates
@app.get("/api/sources")
def sources():
    out = []
    for s in db.rows("SELECT * FROM sources ORDER BY created_at DESC"):
        s["clip_count"] = db.one("SELECT COUNT(*) n FROM clips WHERE source_id=?", (s["id"],))["n"]
        s["candidate_count"] = db.one("SELECT COUNT(*) n FROM candidates WHERE source_id=?", (s["id"],))["n"]
        s["file_exists"] = bool(s["local_path"] and Path(s["local_path"]).exists())
        out.append(s)
    return out


@app.get("/api/sources/{sid}")
def source_get(sid: str):
    s = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "source not found")
    from src.ingestion.rights import publish_allowed
    s["publish_allowed"], s["publish_note"] = publish_allowed(s)
    cands = db.rows("SELECT * FROM candidates WHERE source_id=? ORDER BY virality DESC", (sid,))
    for c in cands:
        c["review"] = json.loads(c.pop("review_json") or "{}")
        c["clip_id"] = (db.one("SELECT id FROM clips WHERE candidate_id=? ORDER BY preview LIMIT 1", (c["id"],)) or {}).get("id")
    s["candidates"] = cands
    s["clips"] = [_clip_summary(c) for c in db.rows("SELECT * FROM clips WHERE source_id=? ORDER BY virality DESC", (sid,))]
    return s


@app.put("/api/sources/{sid}/rights")
def source_rights(sid: str, body: dict = Body(...)):
    from src.ingestion.rights import set_rights
    try:
        set_rights(sid, body.get("rights_status", "unknown"), bool(body.get("commercial_use")),
                   body.get("authorized_on") or None, body.get("authorization_ref") or None,
                   body.get("restrictions") or None, body.get("streamer") or None, body.get("game"))
    except ValueError as e:
        _err(e)
    return source_get(sid)


@app.post("/api/candidates/{cid}/render")
def candidate_render(cid: int):
    c = db.one("SELECT * FROM candidates WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "candidate not found")
    return {"id": jobs.submit("render_candidate", {"candidate_id": cid}, f"Render moment #{cid}")}


# ===================================================================== clips
@app.get("/api/clips")
def clips(status: str | None = None, source: str | None = None, include_previews: bool = True):
    sql, a = "SELECT * FROM clips WHERE 1=1", []
    if status:
        sql += " AND COALESCE(review_status,'pending')=?"
        a.append(status)
    if source:
        sql += " AND source_id=?"
        a.append(source)
    if not include_previews:
        sql += " AND COALESCE(preview,0)=0"
    return [_clip_summary(c) for c in db.rows(sql + " ORDER BY created_at DESC, virality DESC", a)]


@app.get("/api/clips/{clip_id}")
def clip_get(clip_id: str):
    c = _clip_or_404(clip_id)
    out = _clip_summary(c)
    out["metadata"] = json.loads(c["metadata_json"] or "{}")
    src = db.one("SELECT id,title,streamer,profile,game,url,duration,rights_status,commercial_use,restrictions "
                 "FROM sources WHERE id=?", (c["source_id"],))
    from src.ingestion.rights import publish_allowed
    full_src = db.one("SELECT * FROM sources WHERE id=?", (c["source_id"],))
    src["publish_allowed"], src["publish_note"] = publish_allowed(full_src)
    out["source"] = src
    cand = db.one("SELECT summary,why,hook,review_json,reject_reason FROM candidates WHERE id=?", (c["candidate_id"],)) or {}
    out["moment"] = {k: cand.get(k) for k in ("summary", "why", "hook", "reject_reason")}
    out["moment"]["scores"] = json.loads(cand.get("review_json") or "{}").get("scores")
    tr_path = DIRS["transcripts"] / f"{c['source_id']}.json"
    words = []
    if tr_path.exists():
        tr = json.loads(tr_path.read_text(encoding="utf-8"))
        from src.transcription.transcribe import words_between
        words = [w["w"] for w in words_between(tr, c["source_start"], c["source_end"])]
    out["transcript"] = " ".join(words)
    out["locked"] = bool(db.one("SELECT id FROM publications WHERE clip_id=? AND status IN ('uploading','uploaded','published')",
                                (clip_id,)))
    return out


@app.put("/api/clips/{clip_id}/metadata")
def clip_metadata(clip_id: str, body: dict = Body(...)):
    _clip_or_404(clip_id)
    from src.editing.manual import update_metadata
    try:
        return update_metadata(clip_id, body)
    except ValueError as e:
        _err(e)


@app.post("/api/clips/{clip_id}/review")
def clip_review(clip_id: str, body: dict = Body(...)):
    c = _clip_or_404(clip_id)
    status = body.get("status")
    if status not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "status must be approved, rejected or pending")
    if status == "approved" and c["preview"]:
        raise HTTPException(400, "preview renders cannot be approved - render the moment as a real clip first")
    db.execute("UPDATE clips SET review_status=?, updated_at=? WHERE id=?", (status, db.now_iso(), clip_id))
    if status != "approved":
        db.execute("DELETE FROM publications WHERE clip_id=? AND status='scheduled'", (clip_id,))
    sched = None
    if status == "approved" and body.get("schedule", True):
        from src.publishing.scheduler import schedule_all
        sched = schedule_all()
    return {"ok": True, "schedule": sched, "clip": _clip_summary(db.one("SELECT * FROM clips WHERE id=?", (clip_id,)))}


@app.post("/api/clips/{clip_id}/rerender")
def clip_rerender(clip_id: str, body: dict = Body(...)):
    c = _clip_or_404(clip_id)
    args = {"clip_id": clip_id}
    for k in ("start", "end"):
        if body.get(k) not in (None, ""):
            args[k] = float(body[k])
    if "hook" in body:
        args["hook"] = body["hook"] or ""
    if "corrections" in body:
        if not isinstance(body["corrections"], dict):
            raise HTTPException(400, "corrections must be an object")
        args["corrections"] = body["corrections"]
    if args.get("start") is not None and args.get("end") is not None and args["end"] - args["start"] < 3:
        raise HTTPException(400, "clip must be at least 3 seconds long")
    return {"id": jobs.submit("rerender", args, f"Re-render {clip_id}")}


@app.post("/api/clips/{clip_id}/narration")
async def clip_narration(clip_id: str, file: UploadFile = File(...), at: float = 0.0):
    _clip_or_404(clip_id)
    ext = Path(file.filename or "voice.wav").suffix.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm"):
        raise HTTPException(400, "unsupported audio format")
    dest = DIRS["work"] / f"narration_{clip_id}{ext}"
    dest.write_bytes(await file.read())
    from src.editing.narration import add_narration
    try:
        add_narration(clip_id, str(dest), at)
    except Exception as e:
        _err(e)
    return clip_get(clip_id)


@app.post("/api/clips/{clip_id}/schedule")
def clip_schedule(clip_id: str, body: dict = Body(...)):
    """Pin a clip to a specific time on a platform (or the next free slot when no time is given)."""
    c = _clip_or_404(clip_id)
    if c["review_status"] != "approved":
        raise HTTPException(400, "approve the clip first")
    platform = body.get("platform")
    if platform not in ("youtube", "instagram"):
        raise HTTPException(400, "platform must be youtube or instagram")
    src = db.one("SELECT * FROM sources WHERE id=?", (c["source_id"],))
    from src.ingestion.rights import publish_allowed, restriction_blocks
    ok, why = publish_allowed(src)
    block = restriction_blocks(src, platform)
    if not ok or block:
        raise HTTPException(400, why if not ok else block)
    when = body.get("when")
    if not when:
        from src.publishing.scheduler import schedule_all
        return schedule_all()
    t = datetime.fromisoformat(when)
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(cfg()["publishing"]["timezone"]))
    t = t.astimezone(timezone.utc)
    if t < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise HTTPException(400, "time is in the past")
    existing = db.one("SELECT * FROM publications WHERE clip_id=? AND platform=?", (clip_id, platform))
    if existing and existing["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(400, f"already {existing['status']} on {platform}")
    if existing:
        db.execute("UPDATE publications SET status='scheduled', scheduled_for=?, attempts=0, last_error=NULL WHERE id=?",
                   (t.isoformat(), existing["id"]))
    else:
        db.execute("INSERT INTO publications (clip_id,platform,status,scheduled_for) VALUES (?,?,?,?)",
                   (clip_id, platform, "scheduled", t.isoformat()))
    return {"ok": True, "scheduled_for": t.isoformat()}


# ===================================================================== publications / schedule
@app.get("/api/publications")
def publications():
    rows = db.rows("SELECT p.*, c.metadata_json, c.category, c.virality, c.duration FROM publications p "
                   "JOIN clips c ON c.id=p.clip_id ORDER BY p.scheduled_for DESC")
    for r in rows:
        meta = json.loads(r.pop("metadata_json") or "{}")
        r["title"] = (meta.get("youtube") or {}).get("best_title")
    return rows


@app.put("/api/publications/{pid}")
def publication_update(pid: int, body: dict = Body(...)):
    p = db.one("SELECT * FROM publications WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "not found")
    if p["status"] not in ("scheduled", "failed", "blocked"):
        raise HTTPException(400, f"cannot change a {p['status']} publication")
    t = datetime.fromisoformat(body["scheduled_for"])
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(cfg()["publishing"]["timezone"]))
    t = t.astimezone(timezone.utc)
    db.execute("UPDATE publications SET scheduled_for=?, status='scheduled', attempts=0, last_error=NULL WHERE id=?",
               (t.isoformat(), pid))
    return {"ok": True}


@app.delete("/api/publications/{pid}")
def publication_cancel(pid: int):
    p = db.one("SELECT * FROM publications WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "not found")
    if p["status"] in ("uploading", "uploaded", "published"):
        raise HTTPException(400, f"already {p['status']} - remove it on the platform itself")
    db.execute("DELETE FROM publications WHERE id=?", (pid,))
    return {"ok": True}


@app.post("/api/schedule/run")
def schedule_run():
    from src.publishing.scheduler import schedule_all
    _reset_cfg()
    return schedule_all()


@app.post("/api/publish/run")
def publish_run():
    return {"id": jobs.submit("publish", {}, "Publish due clips")}


STATE = ROOT / "config" / "dashboard_state.json"


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"auto_publish": False}


@app.get("/api/autopublish")
def autopublish_get():
    return {"enabled": bool(_state().get("auto_publish"))}


@app.put("/api/autopublish")
def autopublish_put(body: dict = Body(...)):
    st = _state()
    st["auto_publish"] = bool(body.get("enabled"))
    STATE.write_text(json.dumps(st), encoding="utf-8")
    return autopublish_get()


def _autopublish_loop():
    while True:
        time.sleep(300)
        try:
            _reset_cfg()
            if _state().get("auto_publish"):
                busy = db.one("SELECT COUNT(*) n FROM jobs WHERE kind='publish' AND status IN ('queued','running')")["n"]
                due = db.one("SELECT COUNT(*) n FROM publications WHERE status='scheduled' AND scheduled_for<=?",
                             ((datetime.now(timezone.utc) + timedelta(hours=cfg()["publishing"]["youtube"]["upload_lead_hours"])).isoformat(),))["n"]
                if due and not busy:
                    jobs.submit("publish", {}, "Auto-publish")
        except Exception as e:
            log.error("auto-publish loop: %s", e)


# ===================================================================== connections
YT_CHANNEL = DIRS["secrets"] / "youtube_channel.json"
YT_CONNECT = {"state": "idle", "error": None}


@app.get("/api/connections")
def connections():
    from src.publishing import instagram, youtube
    yt = {"client_secret": youtube._client_secrets().exists(), "token": youtube.TOKEN.exists(),
          "connect_state": YT_CONNECT["state"], "connect_error": YT_CONNECT["error"]}
    if YT_CHANNEL.exists():
        yt["channel"] = json.loads(YT_CHANNEL.read_text(encoding="utf-8"))
    ig = {"token": instagram.TOKEN_FILE.exists() or bool(os.getenv("IG_ACCESS_TOKEN"))}
    if instagram.TOKEN_FILE.exists():
        d = json.loads(instagram.TOKEN_FILE.read_text(encoding="utf-8"))
        ig.update({k: d.get(k) for k in ("username", "account_type", "expires_at", "user_id")})
    yt["connected"] = yt["token"] and "channel" in yt
    ig["connected"] = bool(ig.get("username"))
    return {"youtube": yt, "instagram": ig}


@app.post("/api/connections/youtube/client-secret")
async def yt_client_secret(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        j = json.loads(raw)
        assert "installed" in j or "web" in j
    except Exception:
        raise HTTPException(400, "not a Google OAuth client JSON (download it from Credentials > OAuth client)")
    if "installed" not in j:
        raise HTTPException(400, "use an OAuth client of type 'Desktop app'")
    from src.publishing import youtube
    youtube._client_secrets().parent.mkdir(exist_ok=True)
    youtube._client_secrets().write_bytes(raw)
    return connections()


@app.post("/api/connections/youtube/connect")
def yt_connect():
    from src.publishing import youtube
    if not youtube._client_secrets().exists():
        raise HTTPException(400, "upload the OAuth client JSON first")
    if YT_CONNECT["state"] == "waiting":
        return {"state": "waiting"}

    def run():
        YT_CONNECT.update(state="waiting", error=None)
        try:
            info = youtube.whoami(interactive=True)  # opens the Google consent page in this PC's browser
            YT_CHANNEL.write_text(json.dumps(info), encoding="utf-8")
            YT_CONNECT.update(state="connected")
        except Exception as e:
            YT_CONNECT.update(state="error", error=str(e)[:300])

    threading.Thread(target=run, daemon=True).start()
    return {"state": "waiting"}


@app.post("/api/connections/youtube/disconnect")
def yt_disconnect():
    from src.publishing import youtube
    youtube.TOKEN.unlink(missing_ok=True)
    YT_CHANNEL.unlink(missing_ok=True)
    YT_CONNECT.update(state="idle", error=None)
    return connections()


@app.post("/api/connections/instagram")
def ig_connect(body: dict = Body(...)):
    from dotenv import set_key
    from src.publishing import instagram
    token = (body.get("access_token") or "").strip()
    if not token:
        raise HTTPException(400, "access token required")
    env = ROOT / ".env"
    env.touch(exist_ok=True)
    set_key(str(env), "IG_ACCESS_TOKEN", token)
    os.environ["IG_ACCESS_TOKEN"] = token
    if body.get("app_secret"):
        set_key(str(env), "IG_APP_SECRET", body["app_secret"].strip())
        os.environ["IG_APP_SECRET"] = body["app_secret"].strip()
    instagram.TOKEN_FILE.unlink(missing_ok=True)
    try:
        instagram.authorize()
    except Exception as e:
        raise HTTPException(400, f"Instagram rejected the token: {e}")
    return connections()


@app.post("/api/connections/instagram/disconnect")
def ig_disconnect():
    from dotenv import unset_key
    from src.publishing import instagram
    instagram.TOKEN_FILE.unlink(missing_ok=True)
    env = ROOT / ".env"
    if env.exists():
        unset_key(str(env), "IG_ACCESS_TOKEN")
    os.environ.pop("IG_ACCESS_TOKEN", None)
    return connections()


@app.post("/api/connections/{platform}/test")
def connection_test(platform: str, body: dict = Body(...)):
    if platform not in ("youtube", "instagram"):
        raise HTTPException(404, "unknown platform")
    clip = _clip_or_404(body.get("clip_id", ""))
    return {"id": jobs.submit("test_upload", {"platform": platform, "clip_id": clip["id"]},
                              f"Test upload to {platform}: {clip['id']}")}


# ===================================================================== analytics
@app.get("/api/analytics")
def analytics(platform: str | None = None):
    rows = db.rows("""
        SELECT p.id pub_id, p.platform, p.published_at, p.url, p.clip_id, c.category, c.duration, c.metadata_json,
               s.streamer, s.profile, m.views, m.likes, m.comments, m.shares, m.saves, m.avg_view_duration,
               m.avg_view_pct, m.fetched_at
        FROM metrics m
        JOIN (SELECT publication_id, MAX(fetched_at) mf FROM metrics GROUP BY publication_id) l
          ON l.publication_id=m.publication_id AND l.mf=m.fetched_at
        JOIN publications p ON p.id=m.publication_id
        JOIN clips c ON c.id=p.clip_id JOIN sources s ON s.id=c.source_id""")
    if platform:
        rows = [r for r in rows if r["platform"] == platform]
    tz = ZoneInfo(cfg()["publishing"]["timezone"])
    for r in rows:
        meta = json.loads(r.pop("metadata_json") or "{}")
        r["title"] = (meta.get("youtube") or {}).get("best_title")
        inter = sum((r[k] or 0) for k in ("likes", "comments", "shares", "saves"))
        r["engagement_rate"] = round(inter / r["views"], 4) if r["views"] else None
    tot = lambda k: sum((r[k] or 0) for r in rows)  # noqa: E731

    def group(keyf):
        g = defaultdict(list)
        for r in rows:
            if r["views"] is not None:
                g[keyf(r)].append(r)
        out = [{"key": k, "clips": len(v), "median_views": statistics.median(x["views"] for x in v),
                "total_views": sum(x["views"] for x in v)} for k, v in g.items()]
        return sorted(out, key=lambda x: -x["median_views"])

    views = tot("views")
    return {
        "has_data": bool(rows),
        "last_fetch": max((r["fetched_at"] for r in rows), default=None),
        "totals": {"clips": len(rows), "views": views, "likes": tot("likes"), "comments": tot("comments"),
                   "shares": tot("shares"), "saves": tot("saves"),
                   "engagement_rate": round((tot("likes") + tot("comments") + tot("shares") + tot("saves")) / views, 4)
                   if views else None},
        "by_category": group(lambda r: r["category"] or "other"),
        "by_duration": group(lambda r: "<20s" if (r["duration"] or 0) < 20 else "20–35s" if r["duration"] < 35 else "35s+"),
        "by_hour": group(lambda r: datetime.fromisoformat(r["published_at"]).astimezone(tz).strftime("%H:00")
                         if r["published_at"] else "?"),
        "by_streamer": group(lambda r: r["streamer"] or "?"),
        "top": sorted(rows, key=lambda r: -(r["views"] or 0))[:10],
        "learned_weights": db.rows("SELECT * FROM learned_weights ORDER BY key"),
        "published_count": db.one("SELECT COUNT(*) n FROM publications WHERE status='published'")["n"],
    }


@app.post("/api/analytics/fetch")
def analytics_fetch():
    return {"id": jobs.submit("analytics", {}, "Fetch analytics")}


# ===================================================================== media
def _file(path: str | None, media_type: str):
    if not path or not Path(path).exists():
        raise HTTPException(404, "file missing")
    return FileResponse(path, media_type=media_type)


@app.get("/media/clip/{clip_id}")
def media_clip(clip_id: str):
    return _file(_clip_or_404(clip_id)["path"], "video/mp4")


@app.get("/media/source/{sid}")
def media_source(sid: str):
    s = db.one("SELECT local_path FROM sources WHERE id=?", (sid,))
    return _file(s and s["local_path"], "video/mp4")


@app.get("/media/thumb/{clip_id}")
def media_thumb(clip_id: str):
    c = _clip_or_404(clip_id)
    if not c["path"] or not Path(c["path"]).exists():
        raise HTTPException(404, "file missing")
    thumbs = DIRS["work"] / "thumbs"
    thumbs.mkdir(exist_ok=True)
    key = hashlib.sha1(f"{c['path']}{c['sha256']}".encode()).hexdigest()[:12]
    out = thumbs / f"{clip_id}_{key}.jpg"
    if not out.exists():
        t = min(2.0, (c["duration"] or 4) / 2)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", c["path"], "-frames:v", "1",
                        "-vf", "scale=360:-2", "-q:v", "4", str(out)], capture_output=True)
    return _file(str(out), "image/jpeg")


# ===================================================================== static SPA
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main():
    global jobs, PASSWORD
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
    if not _is_loopback(a.host):
        if len(PASSWORD) < 10:
            raise SystemExit("Refusing to listen on a network interface without DASHBOARD_PASSWORD "
                             "(10+ characters) in .env")
        AUTH["required"] = True
        log.warning("Dashboard exposed on %s:%d - password login enabled. Use HTTPS (reverse proxy) "
                    "if this leaves your LAN.", a.host, a.port)
    elif PASSWORD:
        AUTH["required"] = True
    jobs = JobManager()
    threading.Thread(target=_autopublish_loop, daemon=True, name="autopublish").start()
    import uvicorn
    print(f"\n  Clip Studio running at http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}\n")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
