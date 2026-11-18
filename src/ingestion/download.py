"""Ingest a VOD: authorized URL download via yt-dlp, or a creator-provided local file."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .. import db
from ..common import DIRS, get_logger, probe, profile

log = get_logger("ingest")


def _source_id(key: str) -> str:
    return "src_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def ingest(url_or_path: str, streamer: str | None = None, game: str | None = None) -> dict:
    prof = profile()
    game = game if game is not None else (prof.get("game") or "")
    p = Path(url_or_path)
    is_local = p.exists()
    sid = _source_id(str(p.resolve()) if is_local else url_or_path.strip())
    existing = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if existing and existing["local_path"] and Path(existing["local_path"]).exists():
        log.info("Source already ingested: %s", sid)
        return existing

    title = None
    if is_local:
        dest = DIRS["downloads"] / f"{sid}{p.suffix.lower()}"
        if not dest.exists():
            log.info("Copying local file -> %s", dest.name)
            shutil.copy2(p, dest)
        title = p.stem
    else:
        dest, title, uploader = _download(url_or_path, sid)
        streamer = streamer or uploader

    info = probe(dest)
    if not info["has_audio"]:
        log.warning("Source has no audio track - detection will rely on visuals only.")
    with db.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO sources (id,url,local_path,title,streamer,profile,game,duration,created_at,"
            "rights_status,commercial_use,authorized_on,authorization_ref,restrictions,layout_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,"
            "COALESCE((SELECT rights_status FROM sources WHERE id=?),'unknown'),"
            "COALESCE((SELECT commercial_use FROM sources WHERE id=?),0),"
            "(SELECT authorized_on FROM sources WHERE id=?),"
            "(SELECT authorization_ref FROM sources WHERE id=?),"
            "(SELECT restrictions FROM sources WHERE id=?),"
            "(SELECT layout_json FROM sources WHERE id=?))",
            (sid, None if is_local else url_or_path, str(dest), title, streamer, prof["id"], game,
             info["duration"], db.now_iso(), sid, sid, sid, sid, sid, sid))
    log.info("Ingested %s (%.1f min, %dx%d)", sid, info["duration"] / 60, info["width"], info["height"])
    return db.one("SELECT * FROM sources WHERE id=?", (sid,))


def _download(url: str, sid: str):
    import yt_dlp
    out_tmpl = str(DIRS["downloads"] / f"{sid}.%(ext)s")
    opts = {
        "format": "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    log.info("Downloading %s ...", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(DIRS["downloads"].glob(f"{sid}.*"), key=lambda f: f.stat().st_size, reverse=True)
    files = [f for f in files if not re.search(r"\.(part|ytdl|json)$", f.name)]
    if not files:
        raise RuntimeError("Download finished but no media file found")
    return files[0], info.get("title"), info.get("uploader") or info.get("channel")
