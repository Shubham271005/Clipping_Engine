"""Full-stream signal extraction on a 1-second timeline.

Signals: loudness (relative to local baseline), speech rate, hype/laughter lexicon,
pause-then-burst reactions, visual change (motion/cuts) and brightness flashes (explosions).
Also detects a streamer facecam region for smart vertical layouts.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ..common import DIRS, cfg, get_logger, probe

log = get_logger("signals")

def _patterns() -> tuple[re.Pattern, re.Pattern]:
    """Hype/laughter lexicons come from the active content profile."""
    d = cfg()["detection"]
    hype = list(d.get("hype_terms", [])) + list(d.get("extra_hype_terms", []))
    laugh = list(d.get("laugh_terms", [])) + [r"\(laugh\w*\)"]
    return (re.compile(r"\b(" + "|".join(hype) + r")\b", re.I),
            re.compile(r"\b(" + "|".join(laugh) + r")\b", re.I))


def _rolling_median(x: np.ndarray, w: int) -> np.ndarray:
    # cheap rolling median via strided percentile on downsampled blocks
    pad = w // 2
    xp = np.pad(x, pad, mode="edge")
    step = max(1, w // 10)
    idx = np.arange(0, len(x), step)
    med = np.array([np.median(xp[i:i + w]) for i in idx])
    return np.interp(np.arange(len(x)), idx, med)


def _z(x: np.ndarray, w: int = 120) -> np.ndarray:
    base = _rolling_median(x, w)
    dev = _rolling_median(np.abs(x - base), w) + 1e-6
    return (x - base) / (1.4826 * dev)


def audio_loudness(path: Path, duration: float) -> np.ndarray:
    """Per-second loudness in dBFS (max of 4 sub-windows -> catches short screams/gunshots)."""
    sr, n = 8000, int(np.ceil(duration))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                             "-ar", str(sr), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out = np.full(n, -90.0)
    sub = sr // 4
    t = 0
    while t < n:
        buf = proc.stdout.read(sr * 2 * 60)  # 60s at a time
        if not buf:
            break
        a = np.frombuffer(buf[: len(buf) // 2 * 2], np.int16).astype(np.float32) / 32768
        secs = len(a) // sr
        if secs == 0:
            break
        blocks = a[: secs * sr].reshape(secs * 4, sub)
        rms = np.sqrt((blocks ** 2).mean(axis=1) + 1e-10).reshape(secs, 4).max(axis=1)
        k = min(secs, n - t)
        out[t:t + k] = 20 * np.log10(rms[:k])
        t += secs
    proc.stdout.close()
    proc.wait()
    return out


def visual_change(path: Path, duration: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-second visual change + brightness jump using tiny grayscale frames."""
    n = int(np.ceil(duration))
    w, h = 64, 36

    def grab(extra_in: list, vf_rate: str | None):
        vf = (f"{vf_rate}," if vf_rate else "") + f"scale={w}:{h},format=gray,showinfo"
        r = subprocess.run(["ffmpeg", "-v", "info", *extra_in, "-i", str(path), "-an", "-vf", vf,
                            "-f", "rawvideo", "-"], capture_output=True)
        times = [float(m) for m in re.findall(rb"pts_time:([\d.]+)", r.stderr)]
        frames = np.frombuffer(r.stdout, np.uint8)
        cnt = min(len(times), len(frames) // (w * h))
        return np.array(times[:cnt]), frames[: cnt * w * h].reshape(cnt, h, w).astype(np.float32)

    times, frames = grab(["-skip_frame", "nokey"], None)
    if len(times) < duration / 6:  # sparse keyframes -> full decode at 1 fps
        # (CUDA decode was benchmarked slower here because of the per-frame download to CPU)
        times, frames = grab(["-threads", "0"], "fps=1")
    change, flash = np.zeros(n), np.zeros(n)
    if len(frames) < 2:
        return change, flash
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    gap = np.maximum(np.diff(times), 0.5)
    diff = diff / np.sqrt(gap)  # normalize for irregular keyframe spacing
    bright = frames.mean(axis=(1, 2))
    bjump = np.maximum(np.diff(bright), 0)
    for t, d, b in zip(times[1:], diff, bjump):
        i = min(n - 1, int(t))
        change[i] = max(change[i], d)
        flash[i] = max(flash[i], b)
    # fill seconds without frames by interpolation
    have = np.nonzero(change)[0]
    if len(have) > 2:
        change = np.interp(np.arange(n), have, change[have])
    return change, flash


def detect_facecam(path: Path, duration: float, samples: int = 36) -> dict | None:
    """Find a persistent face box (a facecam overlay) across sampled frames."""
    import cv2
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    info = probe(path)
    W, H = info["width"], info["height"]
    if not W:
        return None
    boxes = []
    for k in range(samples):
        t = duration * (k + 0.5) / samples
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=960:-2,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        sh = round(960 * H / W / 2) * 2
        if len(r.stdout) < 960 * sh:
            continue
        img = np.frombuffer(r.stdout[: 960 * sh], np.uint8).reshape(sh, 960)
        faces = casc.detectMultiScale(img, 1.1, 6, minSize=(28, 28))
        for (x, y, fw, fh) in faces:
            boxes.append((x, y, fw, fh))
    if not boxes:
        return None
    # cluster by face centre on a coarse grid; facecams stay put, game NPC faces move
    sc = W / 960
    grid = {}
    for b in boxes:
        key = (int((b[0] + b[2] / 2) // 60), int((b[1] + b[3] / 2) // 60))
        grid.setdefault(key, []).append(b)
    key, members = max(grid.items(), key=lambda kv: len(kv[1]))
    hit_rate = len(members) / samples
    if hit_rate < 0.35:
        return None
    x, y, fw, fh = np.median(np.array(members), axis=0) * sc
    cx, cy = x + fw / 2, y + fh / 2
    # facecam region: expand face box (typical overlay ~3.2x face width, 4:3)
    rw = min(W, fw * 3.0)
    rh = min(H, rw * 0.75)
    rx = int(np.clip(cx - rw / 2, 0, W - rw))
    ry = int(np.clip(cy - rh * 0.45, 0, H - rh))
    central = W * 0.3 < cx < W * 0.7 and H * 0.3 < cy < H * 0.7
    if central:  # a face in the middle of the screen is gameplay / full-cam, not an overlay
        return None
    return {"x": rx, "y": ry, "w": int(rw) // 2 * 2, "h": int(rh) // 2 * 2, "hit_rate": round(hit_rate, 2)}


def speech_features(transcript: dict, n: int) -> dict:
    HYPE, LAUGH = _patterns()
    rate, hype, laugh, conf = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for s in transcript["segments"]:
        for w in s["words"]:
            i = min(n - 1, int(w["s"]))
            rate[i] += 1
            conf[i] = max(conf[i], w["p"])
        txt = s["text"]
        i = min(n - 1, int(s["s"]))
        hype[i] += len(HYPE.findall(txt)) + txt.count("!") * 0.5
        laugh[i] += len(LAUGH.findall(txt))
    return {"rate": rate, "hype": hype, "laugh": laugh, "conf": conf}


def extract(source: dict, transcript: dict, force: bool = False) -> dict:
    cache = DIRS["work"] / f"{source['id']}_signals.npz"
    path = Path(source["local_path"])
    dur = source["duration"]
    n = int(np.ceil(dur))
    if cache.exists() and not force:
        d = dict(np.load(cache))
    else:
        log.info("Analysing audio loudness over %.1f min ...", dur / 60)
        loud = audio_loudness(path, dur)
        log.info("Analysing visual activity ...")
        change, flash = visual_change(path, dur)
        d = {"loud": loud, "change": change, "flash": flash}
        np.savez_compressed(cache, **d)
    sp = speech_features(transcript, n)

    loud_z = np.clip(_z(d["loud"]), -3, 6)
    vis_z = np.clip(_z(d["change"]), -3, 6)
    flash_z = np.clip(_z(d["flash"] + 1e-3), 0, 6)
    rate_s = np.convolve(sp["rate"], np.ones(3) / 3, mode="same")
    rate_z = np.clip(_z(rate_s), -3, 5)
    quiet = (d["loud"] < np.percentile(d["loud"], 35)) & (sp["rate"] == 0)
    q_run = np.convolve(quiet.astype(float), np.ones(3), mode="full")[:n]  # 3s quiet before t
    burst = np.zeros(n)
    burst[1:] = ((np.roll(q_run, 1)[1:] >= 2.5) & (loud_z[1:] > 1.5)).astype(float)

    w = cfg()["detection"]["signal_weights"]
    interest = (w["loudness"] * np.maximum(loud_z, 0) + w["hype_words"] * np.minimum(sp["hype"], 4)
                + w["laughter"] * np.minimum(sp["laugh"], 3) + w["speech_rate"] * np.maximum(rate_z, 0)
                + w["visual_change"] * np.maximum(vis_z, 0) + w["flash"] * flash_z
                + w["pause_then_burst"] * burst)
    interest = np.convolve(interest, np.ones(5) / 5, mode="same")
    d.update({"loud_z": loud_z, "vis_z": vis_z, "rate_z": rate_z, "burst": burst,
              "hype": sp["hype"], "laugh": sp["laugh"], "interest": interest, "quiet": quiet})

    if source.get("layout_json") is None:
        log.info("Detecting facecam ...")
        fc = detect_facecam(path, dur)
        from .. import db
        db.execute("UPDATE sources SET layout_json=? WHERE id=?", (json.dumps({"facecam": fc}), source["id"]))
        source["layout_json"] = json.dumps({"facecam": fc})
        log.info("Facecam: %s", fc or "none detected")
    return d
