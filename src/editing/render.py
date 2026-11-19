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
           corrections: dict | None = None, hook_text: str = "", layout_override=None) -> dict:
    src = Path(source["local_path"])
    info = probe(src)
    W, H = info["width"], info["height"]
    fps = min(60.0, round(info["fps"]) or 30)
    work = DIRS["work"] / clip_id
    work.mkdir(parents=True, exist_ok=True)

    # 1) smart-cut intermediate
    segs = plan_segments(transcript, sig, cand["start"], cand["end"])
    base = max(0.0, cand["start"] - 2.0)
    # snap every cut to a frame boundary: video can only cut on frames while audio cuts sample-exact,
    # so unsnapped cuts accumulate A/V drift (QC measured ~100 ms on clips with several cuts)
    snap = lambda t: base + round((t - base) * fps) / fps  # noqa: E731
    segs = [(snap(a), snap(b)) for a, b in segs]
    segs = [(a, b) for a, b in segs if b - a >= 2 / fps]
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

    # 3) layout - analysed per clip on the source footage of THIS moment (facecam can move/resize)
    from .layout import LayoutPlan, Rect, analyze, fc_fit
    e = cfg()["editing"]
    if layout_override is not None:
        plan = layout_override
    else:
        plan = analyze(src, cand["start"], cand["end"], W, H)
    zoom_t = punch_in_times(cut, e["punch_ins"])
    zexpr = _zoom_expr(zoom_t)
    ass = DIRS["subtitles"] / f"{clip_id}.ass"
    hook_until = None

    def gameplay_chain(label_in: str, out_w: int, out_h: int, label_out: str) -> str:
        g = plan.gameplay
        chain = f"[{label_in}]crop={g.w}:{g.h}:{g.x}:{g.y}"
        if plan.gameplay_mask:  # facecam unavoidable inside the crop -> blur it so it never shows twice
            m = plan.gameplay_mask
            chain += (f",split=2[gm1][gm2];[gm2]crop={max(2, m.w // 2 * 2)}:{max(2, m.h // 2 * 2)}:{m.x}:{m.y},"
                      f"boxblur=30:4[gmb];[gm1][gmb]overlay={m.x}:{m.y}")
        for k, br in enumerate(plan.blur_rects or []):  # stream chat -> soft blur so it doesn't clutter
            bw, bh = max(2, br["w"] // 2 * 2), max(2, br["h"] // 2 * 2)
            chain += (f",split=2[cb{k}a][cb{k}b];[cb{k}b]crop={bw}:{bh}:{br['x']}:{br['y']},boxblur=18:3[cb{k}c];"
                      f"[cb{k}a][cb{k}c]overlay={br['x']}:{br['y']}")
        chain += (f",scale={out_w}:{out_h},zoompan=z='{zexpr}':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
                  f":s={out_w}x{out_h}:fps={fps},setsar=1[{label_out}]")
        return chain

    if plan.kind == "facecam_stack":
        fc, hb = plan.facecam, plan.fc_band_h
        ox, oy, fw, fh = fc_fit(plan)
        gb = H_OUT - hb
        vgraph = (
            f"[0:v]split=3[fa][fb][g];"
            # blurred, darkened copy of the facecam fills the band (no black bars, nobody cropped)
            f"[fa]crop={fc.w}:{fc.h}:{fc.x}:{fc.y},scale=270:{hb // 4 // 2 * 2}:force_original_aspect_ratio=increase,"
            f"crop=270:{hb // 4 // 2 * 2},boxblur=10:2,scale={W_OUT}:{hb},eq=brightness=-0.22:saturation=0.9,setsar=1[fcbg];"
            # the COMPLETE facecam, fitted (contain) - every person stays in frame
            f"[fb]crop={fc.w}:{fc.h}:{fc.x}:{fc.y},scale={fw}:{fh},setsar=1[fcfg];"
            f"[fcbg][fcfg]overlay={ox}:{oy}[fcv];"
            + gameplay_chain("g", W_OUT, gb, "gpv") + ";"
            f"[fcv][gpv]vstack=inputs=2,drawbox=x=0:y={hb - 2}:w={W_OUT}:h=4:color=black@0.55:t=fill[stk]")
        hook_until = None  # seam phrase stays for the whole clip
        layout_name = "facecam_stack"
    elif plan.kind == "camera":
        g = plan.gameplay
        vgraph = (f"[0:v]crop={g.w}:{g.h}:{g.x}:{g.y},scale={W_OUT}:{H_OUT},setsar=1[stk]")
        layout_name = "camera"
    else:  # gameplay_focus / camera_fit: full subject on a blurred fill
        g = plan.gameplay
        gh = int(W_OUT * g.h / g.w) // 2 * 2
        gy = max(0, (H_OUT - gh) // 2 - 120) // 2 * 2
        vgraph = (
            f"[0:v]split=2[bg][g];"
            f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:3,"
            f"scale={W_OUT}:{H_OUT},eq=brightness=-0.15:saturation=1.05,setsar=1[bgv];"
            + gameplay_chain("g", W_OUT, gh, "gv") + ";"
            f"[bgv][gv]overlay=0:{gy}[stk]")
        plan.caption_y = min(H_OUT - 260, gy + gh + 150)
        plan.hook_y = max(120, gy - 110)
        layout_name = plan.kind

    build_ass(words, ass, cut_dur, plan.caption_y, hook_text=hook_text, hook_y=plan.hook_y,
              hook_until=hook_until, corrections=corrections)
    fonts = e["fonts_dir"].replace("\\", "/").replace(":", r"\:")
    fade_out = max(0.0, cut_dur - 0.25)
    vgraph += (f";[stk]ass=filename='{ass.name}':fontsdir='{fonts}',"
               f"fade=t=out:st={fade_out:.2f}:d=0.25,format=yuv420p[vout]")  # no fade-in: frame 1 is the cover/hook

    # audio: optional subtle whoosh on punch-ins (generated locally - no licensing issues), then loudness
    inputs = ["-i", cut]
    sfx_times = zoom_t if e.get("sfx", True) else []
    if sfx_times:
        inputs += ["-i", _whoosh()]
        n = len(sfx_times)
        agraph = f"[1:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n)) + ";" if n > 1 else "[1:a]anull[s0];"
        for i, t in enumerate(sfx_times):
            d = int(max(0, t - 0.18) * 1000)
            agraph += f"[s{i}]adelay={d}|{d},volume=0.35[d{i}];"
        agraph += "[0:a]" + "".join(f"[d{i}]" for i in range(n)) + f"amix=inputs={n + 1}:normalize=0:duration=first,"
    else:
        agraph = "[0:a]"
    # pad/trim to the exact video length: mixing the SFX left ~100 ms of extra audio (QC caught the drift)
    vdur = float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                                 "-of", "csv=p=0", str(cut)], capture_output=True, text=True).stdout.strip() or cut_dur)
    agraph += (f"loudnorm=I={e['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000,apad,atrim=end={vdur:.3f},"
               f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.1,afade=t=out:st={max(0, vdur - 0.3):.2f}:d=0.3[aout]")
    out = DIRS["final_clips"] / f"{clip_id}.mp4"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", vgraph + ";" + agraph,
         "-map", "[vout]", "-map", "[aout]", "-r", f"{fps}", *video_encoder_args(19),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out],
        cwd=str(ass.parent))
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    res = {"path": str(out), "duration": round(probe(out)["duration"], 2), "layout": layout_name,
           "sha256": sha, "dead_air_removed_s": round(removed, 2), "punch_ins": zoom_t,
           "segments": segs, "plan": plan.to_dict(), "cut_duration": round(cut_dur, 2),
           "captions": len(words)}
    log.info("Rendered %s (%.1fs, %s, %d people in facecam, -%.1fs dead air, %d punch-ins)", out.name,
             res["duration"], layout_name, plan.people, removed, len(zoom_t))
    return res


def _whoosh() -> str:
    """A short, soft whoosh synthesised with ffmpeg (original audio, safe to publish)."""
    p = DIRS["work"] / "sfx_whoosh.wav"
    if not p.exists():
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.55:amplitude=0.7",
             "-af", "highpass=f=300,lowpass=f=4500,afade=t=in:d=0.28:curve=exp,afade=t=out:st=0.28:d=0.27,"
             "volume=0.6,aresample=48000", "-ac", "2", str(p)])
    return str(p)
