"""Mix YOUR recorded original narration into a clip (originality for reused-content policies).

The original audio is ducked under the narration with a sidechain compressor.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .. import db
from ..common import DIRS, get_logger, probe, run

log = get_logger("narration")


def add_narration(clip_id: str, narration_file: str, at: float = 0.0, narration_gain_db: float = 2.0) -> str:
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise ValueError(f"unknown clip {clip_id}")
    if db.one("SELECT id FROM publications WHERE clip_id=? AND status IN ('uploading','uploaded','published')",
              (clip_id,)):
        raise ValueError("clip already uploaded - narration must be added before publishing")
    src = Path(clip["path"])
    orig = DIRS["final_clips"] / f"{clip_id}.original.mp4"
    if not orig.exists():
        shutil.copy2(src, orig)
    narr = Path(narration_file)
    nd = probe(narr)["duration"]
    vd = probe(orig)["duration"]
    if at + nd > vd + 0.5:
        log.warning("Narration (%.1fs) runs past the clip end (%.1fs) and will be cut.", nd, vd)
    delay = int(at * 1000)
    graph = (f"[1:a]aresample=48000,volume={narration_gain_db}dB,adelay={delay}|{delay},apad,asplit=2[n1][n2];"
             f"[0:a][n1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[duck];"
             f"[duck][n2]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    out = DIRS["final_clips"] / f"{clip_id}.narrated.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", orig, "-i", narr, "-filter_complex", graph,
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", out])
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    db.execute("UPDATE clips SET path=?, sha256=?, narrated=1 WHERE id=?", (str(out), sha, clip_id))
    log.info("Narration added -> %s", out.name)
    return str(out)
