"""Render a selected moment into a polished 1080x1920 H.264/AAC short."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from .. import db
from ..common import DIRS, cfg, decode_audio, get_logger, probe, run, video_encoder_args
from ..transcription.transcribe import words_between
from .captions import build_ass

log = get_logger("render")
W_OUT, H_OUT = 1080, 1920
FC_H = 700           # facecam band height in facecam layout
GP_H = H_OUT - FC_H  # gameplay band height in facecam layout


# ------------------------------------------------------------------ smart cuts
def plan_segments(transcript: dict, sig: dict, start: float, end: float) -> list[tuple[float, float]]:
    """Tighten dead air: silent, low-action gaps longer than smart_cut_gap_s shrink to ~0.3s."""
    gap_max = cfg()["editing"]["smart_cut_gap_s"]
    step = 0.1
    t = np.arange(start, end, step)
    keep = np.zeros(len(t), bool)
    for w in words_between(transcript, start, end):
        keep[(t >= w["s"] - 0.2) & (t <= w["e"] + 0.25)] = True
    sec = np.clip(t.astype(int), 0, len(sig["loud_z"]) - 1)
    keep |= sig["loud_z"][sec] > 0.6     # loud game audio / reactions
    keep |= sig["vis_z"][sec] > 0.8      # visual action without speech is still content
    segs, i = [], 0
    cur_s = start
    while i < len(t):
        if not keep[i]:
            j = i
            while j < len(t) and not keep[j]:
                j += 1
            gs, ge = t[i], (t[j] if j < len(t) else end)
            if ge - gs > gap_max and i > 0 and j < len(t):
                segs.append((cur_s, gs + 0.15))
                cur_s = ge - 0.15
            i = j
        else:
            i += 1
    segs.append((cur_s, end))
    return [(round(a, 2), round(b, 2)) for a, b in segs if b - a > 0.2]


def map_time(t: float, segs: list[tuple[float, float]]) -> float | None:
    acc = 0.0
    for a, b in segs:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


# ------------------------------------------------------------------ layout helpers
def gameplay_crop_x(path: Path, W: int, H: int, crop_w: int, facecam: dict | None) -> int:
    """Pick a horizontal crop centre from where motion happens (biased to centre for stability)."""
    w, h = 160, 90
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf",
                        f"fps=3,scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    fr = np.frombuffer(r.stdout, np.uint8)
    n = len(fr) // (w * h)
    centre = W / 2
    if n >= 3:
        f = fr[: n * w * h].reshape(n, h, w).astype(np.float32)
        motion = np.abs(np.diff(f, axis=0)).mean(axis=0)
        if facecam:
            sx, sy = w / W, h / H
            motion[int(facecam["y"] * sy):int((facecam["y"] + facecam["h"]) * sy),
                   int(facecam["x"] * sx):int((facecam["x"] + facecam["w"]) * sx)] = 0
        col = motion.sum(axis=0)
        if col.sum() > 0:
            cx = (col * np.arange(w)).sum() / col.sum() * (W / w)
            centre = 0.55 * cx + 0.45 * (W / 2)
    return int(np.clip(centre - crop_w / 2, 0, W - crop_w)) // 2 * 2


def punch_in_times(audio_path: Path, n_max: int) -> list[float]:
    if n_max <= 0:
        return []
    a = decode_audio(audio_path, 8000)
    if a.size < 8000 * 3:
        return []
    blk = 2000  # 0.25 s
    nb = a.size // blk
    rms = np.sqrt((a[: nb * blk].reshape(nb, blk) ** 2).mean(axis=1) + 1e-10)
    db_ = 20 * np.log10(rms)
    z = (db_ - np.median(db_)) / (np.std(db_) + 1e-6)
    out = []
    for i in np.argsort(-z):
        t = i * 0.25
        if z[i] < 1.8 or len(out) >= n_max:
            break
        if t < 1.5 or t > nb * 0.25 - 1.5:
            continue
        if all(abs(t - o) > 5 for o in out):
            out.append(round(float(t), 2))
    return sorted(out)


def _zoom_expr(times: list[float]) -> str:
    if not times:
        return "1"
    terms = "+".join(f"between(in_time,{t - 0.1:.2f},{t + 0.9:.2f})" for t in times)
    return f"1+0.13*min(1,{terms})"


# ------------------------------------------------------------------ main render
def render(source: dict, transcript: dict, sig: dict, cand: dict, clip_id: str,
           corrections: dict | None = None, hook_text: str = "") -> dict:
    src = Path(source["local_path"])
    info = probe(src)
    W, H = info["width"], info["height"]
    fps = min(60.0, round(info["fps"]) or 30)
    work = DIRS["work"] / clip_id
    work.mkdir(parents=True, exist_ok=True)

    # 1) smart-cut intermediate
    segs = plan_segments(transcript, sig, cand["start"], cand["end"])
    base = max(0.0, cand["start"] - 2.0)
    fc_parts, labels = [], []
    for i, (a, b) in enumerate(segs):
        fc_parts.append(f"[0:v]trim={a - base:.3f}:{b - base:.3f},setpts=PTS-STARTPTS[v{i}];"
                        f"[0:a]atrim={a - base:.3f}:{b - base:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(fc_parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    cut = work / "cut.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{base:.3f}", "-t", f"{cand['end'] - base + 0.5:.3f}",
         "-i", src, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-r", f"{fps}",
         *video_encoder_args(16), "-c:a", "aac", "-b:a", "256k", cut])
    cut_dur = probe(cut)["duration"]
    removed = (cand["end"] - cand["start"]) - cut_dur

    # 2) words re-timed to the cut timeline
    words = []
    for w in words_between(transcript, cand["start"], cand["end"]):
        s, e = map_time(w["s"], segs), map_time(w["e"], segs)
        if s is None:
            continue
        words.append({**w, "s": s, "e": e if e is not None else s + 0.3})

    # 3) layout
    layout = json.loads(source.get("layout_json") or "{}").get("facecam")
    e = cfg()["editing"]
    zoom_t = punch_in_times(cut, e["punch_ins"])
    zexpr = _zoom_expr(zoom_t)
    ass = DIRS["subtitles"] / f"{clip_id}.ass"
    if layout:
        fc = layout
        gp_cw = min(W, int(H * W_OUT / GP_H)) // 2 * 2
        gx = gameplay_crop_x(cut, W, H, gp_cw, fc)
        cap_y, hook_y, hook_until = FC_H + 90, FC_H - 90, 3.0
        vgraph = (
            f"[0:v]split=2[fc][gp];"
            f"[fc]crop={fc['w']}:{fc['h']}:{fc['x']}:{fc['y']},"
            f"scale={W_OUT}:{FC_H}:force_original_aspect_ratio=increase,crop={W_OUT}:{FC_H},"
            f"zoompan=z='{zexpr}':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={W_OUT}x{FC_H}:fps={fps}[fcv];"
            f"[gp]crop={gp_cw}:{H}:{gx}:0,scale={W_OUT}:{GP_H},setsar=1[gpv];"
            f"[fcv][gpv]vstack=inputs=2[stk]")
        layout_name = "facecam_stack"
    else:
        asp = e["gameplay_layout_aspect"]
        gh = int(W_OUT / asp) // 2 * 2
        cw = min(W, int(H * asp)) // 2 * 2
        gx = gameplay_crop_x(cut, W, H, cw, None)
        gy = 400
        cap_y, hook_y, hook_until = gy + gh + 170, 250, None
        vgraph = (
            f"[0:v]split=2[bg][g];"
            f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:3,"
            f"scale={W_OUT}:{H_OUT},eq=brightness=-0.12:saturation=1.1,setsar=1[bgv];"
            f"[g]crop={cw}:{H}:{gx}:0,scale={W_OUT}:{gh},"
            f"zoompan=z='{zexpr}':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={W_OUT}x{gh}:fps={fps},setsar=1[gv];"
            f"[bgv][gv]overlay=0:{gy}[stk]")
        layout_name = "gameplay_focus"

    build_ass(words, ass, cut_dur, cap_y, hook_text=hook_text, hook_y=hook_y,
              hook_until=hook_until, corrections=corrections)
    fonts = e["fonts_dir"].replace("\\", "/").replace(":", "\\:")
    vgraph += f";[stk]ass=filename='{ass.name}':fontsdir='{fonts}',format=yuv420p[vout]"
    agraph = (f"[0:a]loudnorm=I={e['loudness_lufs']}:TP=-1.5:LRA=11,"
              f"aresample=48000,afade=t=out:st={max(0, cut_dur - 0.3):.2f}:d=0.3[aout]")
    out = DIRS["final_clips"] / f"{clip_id}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", cut, "-filter_complex", vgraph + ";" + agraph,
         "-map", "[vout]", "-map", "[aout]", "-r", f"{fps}", *video_encoder_args(19),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out],
        cwd=str(ass.parent))
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    res = {"path": str(out), "duration": round(probe(out)["duration"], 2), "layout": layout_name,
           "sha256": sha, "dead_air_removed_s": round(removed, 2), "punch_ins": zoom_t,
           "segments": segs}
    log.info("Rendered %s (%.1fs, %s, -%.1fs dead air, %d punch-ins)", out.name, res["duration"],
             layout_name, removed, len(zoom_t))
    return res
