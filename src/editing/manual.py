"""Human-in-the-loop edits: re-render a clip with new trim/hook/caption fixes, or promote any
detected moment (even one the AI rejected) into a clip."""
from __future__ import annotations

import json

from .. import db
from ..common import DIRS, get_logger, set_profile
from ..detection import signals
from ..metadata.generate import _validate, generate
from ..transcription.transcribe import transcribe
from .render import render

log = get_logger("manual")


def _load(source_id: str):
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        raise ValueError(f"unknown source {source_id}")
    set_profile(src.get("profile") or "gta6")
    tr = transcribe(src)
    sig = signals.extract(src, tr)
    return db.one("SELECT * FROM sources WHERE id=?", (source_id,)), tr, sig


def _assert_editable(clip_id: str) -> None:
    if db.one("SELECT id FROM publications WHERE clip_id=? AND status IN ('uploading','uploaded','published')",
              (clip_id,)):
        raise ValueError("clip is already uploaded - it can no longer be re-rendered")


def rerender_clip(clip_id: str, start: float | None = None, end: float | None = None,
                  hook: str | None = None, corrections: dict | None = None) -> dict:
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError(f"unknown clip {clip_id}")
    _assert_editable(clip_id)
    src, tr, sig = _load(clip["source_id"])
    meta = json.loads(clip["metadata_json"])
    st = clip["source_start"] if start is None else max(0.0, float(start))
    en = clip["source_end"] if end is None else min(src["duration"], float(end))
    if en - st < 3:
        raise ValueError("clip must be at least 3 seconds")
    if hook is not None:
        meta["on_screen_hook"] = hook
    if corrections is not None:
        meta["caption_corrections"] = corrections
    cand = {"start": st, "end": en}
    res = render(src, tr, sig, cand, clip_id, corrections=meta.get("caption_corrections"),
                 hook_text=meta.get("on_screen_hook") or "")
    meta["render"] = {k: res[k] for k in ("layout", "dead_air_removed_s", "punch_ins")}
    (DIRS["metadata"] / f"{clip_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    db.execute("UPDATE clips SET path=?, sha256=?, duration=?, layout=?, source_start=?, source_end=?, metadata_json=?, "
               "narrated=0, updated_at=? WHERE id=?",
               (res["path"], res["sha256"], res["duration"], res["layout"], st, en, db.dumps(meta), db.now_iso(), clip_id))
    log.info("Re-rendered %s [%.1f-%.1f]", clip_id, st, en)
    return {"clip_id": clip_id, **res}


def render_candidate(candidate_id: int) -> dict:
    """Turn any detected moment into a real clip (human override of the AI decision)."""
    c = db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if not c:
        raise ValueError(f"unknown candidate {candidate_id}")
    src, tr, sig = _load(c["source_id"])
    existing = db.one("SELECT id FROM clips WHERE candidate_id=? AND COALESCE(preview,0)=0", (candidate_id,))
    if existing:
        return rerender_clip(existing["id"])
    n = db.one("SELECT COUNT(*) n FROM clips WHERE source_id=?", (src["id"],))["n"]
    clip_id = f"{src['id']}_m{candidate_id}"
    rj = json.loads(c["review_json"] or "{}")
    cand = {**c, "on_screen_hook": rj.get("on_screen_hook", "")}
    meta = generate(src, tr, cand, clip_id)
    res = render(src, tr, sig, cand, clip_id, corrections=meta.get("caption_corrections"),
                 hook_text=meta.get("on_screen_hook") or cand["on_screen_hook"])
    meta["render"] = {k: res[k] for k in ("layout", "dead_air_removed_s", "punch_ins")}
    (DIRS["metadata"] / f"{clip_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    db.execute("INSERT OR REPLACE INTO clips (id,source_id,candidate_id,path,sha256,duration,layout,category,virality,"
               "source_start,source_end,metadata_json,commentary_json,created_at,preview,review_status) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'pending')",
               (clip_id, src["id"], candidate_id, res["path"], res["sha256"], res["duration"], res["layout"],
                c["category"], c["virality"], c["start"], c["end"], db.dumps(meta),
                db.dumps(meta.get("commentary") or {}), db.now_iso()))
    db.execute("UPDATE candidates SET status='rendered' WHERE id=?", (candidate_id,))
    log.info("Manual clip %s created from candidate %d (clip #%d of source)", clip_id, candidate_id, n + 1)
    return {"clip_id": clip_id, **res}


def update_metadata(clip_id: str, patch: dict) -> dict:
    """Apply human edits to titles/descriptions/hashtags and recompose the final strings."""
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError(f"unknown clip {clip_id}")
    src = db.one("SELECT * FROM sources WHERE id=?", (clip["source_id"],))
    set_profile(src.get("profile") or "gta6")
    meta = json.loads(clip["metadata_json"])
    yt, ig = meta["youtube"], meta["instagram"]
    y = patch.get("youtube") or {}
    for k in ("best_title", "description"):
        if k in y:
            yt[k] = str(y[k]).strip()
    if "titles" in y:
        yt["titles"] = [t for t in y["titles"] if t.strip()]
    for k in ("tags", "hashtags"):
        if k in y:
            yt[k] = [t.strip() for t in y[k] if t.strip()]
    i = patch.get("instagram") or {}
    for k in ("caption", "cta"):
        if k in i:
            ig[k] = str(i[k]).strip()
    if "hashtags" in i:
        ig["hashtags"] = [t.strip() for t in i["hashtags"] if t.strip()]
    if not yt.get("best_title"):
        raise ValueError("YouTube title cannot be empty")
    if len(yt["best_title"]) > 100:
        raise ValueError("YouTube titles are limited to 100 characters")
    meta = _validate(meta, src)
    db.execute("UPDATE clips SET metadata_json=?, updated_at=? WHERE id=?", (db.dumps(meta), db.now_iso(), clip_id))
    (DIRS["metadata"] / f"{clip_id}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta
