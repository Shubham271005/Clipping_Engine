"""Per-clip layout analysis: find the COMPLETE facecam (every person in it), decide the vertical
composition, and pick a gameplay crop that never contains the facecam (no duplicates).

Root-cause notes (why this exists):
- The old detector clustered single Haar faces over the whole stream and cropped around the most
  stable one -> it locked onto one person (e.g. Speed's brother) and cut the streamer out.
- The gameplay crop was taken from the frame centre/motion and often contained the facecam overlay,
  showing the facecam twice.
- Detection ran once per stream, but streamers resize/move their facecam mid-stream.

Approach: sample frames from the clip itself, detect ALL faces with YuNet, drop static faces (game
characters in a still shot are pixel-identical frame to frame; real people jitter), treat every real
person as part of ONE facecam group, and grow that group to the overlay's actual borders using
persistent straight edges averaged over time (overlay borders stay put, content moves).
"""
from __future__ import annotations

import subprocess
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..common import ROOT, get_logger

log = get_logger("layout")
AW, AH = 960, 540  # analysis resolution (16:9); rects are converted back to source pixels
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
YUNET_PATH = ROOT / "models" / "face_detection_yunet_2023mar.onnx"

OUT_W, OUT_H = 1080, 1920
FC_MIN_H, FC_MAX_H = 480, 864        # facecam band = 25%..45% of the frame, chosen from the facecam's own shape


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self): return self.x + self.w

    @property
    def y2(self): return self.y + self.h

    def scaled(self, sx, sy) -> "Rect":
        return Rect(int(round(self.x * sx)), int(round(self.y * sy)), int(round(self.w * sx)), int(round(self.h * sy)))

    def even(self, W, H) -> "Rect":
        x, y = max(0, self.x) // 2 * 2, max(0, self.y) // 2 * 2
        w = min(W - x, self.w) // 2 * 2
        h = min(H - y, self.h) // 2 * 2
        return Rect(x, y, w, h)

    def inter(self, o: "Rect") -> int:
        return max(0, min(self.x2, o.x2) - max(self.x, o.x)) * max(0, min(self.y2, o.y2) - max(self.y, o.y))


@dataclass
class LayoutPlan:
    kind: str                         # facecam_stack | gameplay_focus | camera
    src_w: int
    src_h: int
    facecam: Rect | None = None       # complete facecam region in source pixels
    people: int = 0                   # distinct real people found in the facecam
    person_boxes: list = field(default_factory=list)  # typical face boxes (source px)
    fc_band_h: int = 0                # output facecam band height
    gameplay: Rect | None = None      # gameplay crop in source pixels
    gameplay_mask: Rect | None = None  # part of the gameplay crop that overlaps the facecam -> blurred
    blur_rects: list = field(default_factory=list)  # stream chat inside the gameplay crop (crop coords) -> blurred
    caption_y: int = 0
    hook_y: int = 0
    notes: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d


# ------------------------------------------------------------------ detector
_DET = None


def detector():
    global _DET
    if _DET is None:
        import cv2
        if not YUNET_PATH.exists():
            YUNET_PATH.parent.mkdir(exist_ok=True)
            log.info("Downloading YuNet face detector (~230 KB) ...")
            urllib.request.urlretrieve(YUNET_URL, YUNET_PATH)
        _DET = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (AW, AH), 0.62, 0.3, 5000)
    return _DET


def detect_faces(img: np.ndarray, min_size: int = 14) -> list[tuple]:
    """Faces as (x, y, w, h, score) in the image's pixel space."""
    det = detector()
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(img)
    if f is None:
        return []
    return [(float(a[0]), float(a[1]), float(a[2]), float(a[3]), float(a[14])) for a in f if a[2] >= min_size]


def grab_frames(path: Path, times: list[float], w: int = AW, h: int = AH) -> list[np.ndarray]:
    frames = []
    for t in times:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
        if len(r.stdout) >= w * h * 3:
            frames.append(np.frombuffer(r.stdout[: w * h * 3], np.uint8).reshape(h, w, 3))
    return frames


# ------------------------------------------------------------------ people clustering
def _cluster_people(faces_per_frame: list[list[tuple]], n_frames: int) -> list[dict]:
    clusters: list[dict] = []
    for fi, faces in enumerate(faces_per_frame):
        for (x, y, w, h, s) in faces:
            cx, cy = x + w / 2, y + h / 2
            best, bd = None, 1e9
            for c in clusters:
                mx, my, mw = np.mean(c["cx"]), np.mean(c["cy"]), np.mean(c["w"])
                d = np.hypot(cx - mx, cy - my)
                if d < max(mw, w) * 1.1 and d < bd:
                    best, bd = c, d
            if best is None:
                best = {"cx": [], "cy": [], "w": [], "h": [], "x": [], "y": [], "frames": set(), "score": []}
                clusters.append(best)
            for k, v in (("cx", cx), ("cy", cy), ("w", w), ("h", h), ("x", x), ("y", y), ("score", s)):
                best[k].append(v)
            best["frames"].add(fi)
    people = []
    for c in clusters:
        hit = len(c["frames"]) / max(1, n_frames)
        jitter = float(np.std(c["cx"]) + np.std(c["cy"]) + np.std(c["w"]))
        c.update(hit=hit, jitter=jitter)
        # real people: present in a good share of frames AND not pixel-static (static = game art / still shot)
        if hit >= 0.3 and jitter >= 1.2:
            people.append(c)
    return people


def _group_with_anchor(people: list[dict]) -> tuple[list[dict], list[dict]]:
    """The facecam group = the most present person plus everyone sitting next to them (chained).
    Game characters elsewhere on screen are not part of the streamer's facecam."""
    anchor = max(people, key=lambda c: c["hit"] * np.median(c["w"]))
    group, changed = [anchor], True
    while changed:
        changed = False
        for c in people:
            if any(c is g for g in group):
                continue
            for g in group:
                d = np.hypot(np.mean(c["cx"]) - np.mean(g["cx"]), np.mean(c["cy"]) - np.mean(g["cy"]))
                fw = (np.median(c["w"]) + np.median(g["w"])) / 2
                if d <= 3.8 * fw:
                    group.append(c)
                    changed = True
                    break
    return group, [c for c in people if not any(c is g for g in group)]


def _person_box(c: dict) -> Rect:
    """Generous box around a person: head, hair, shoulders - using robust percentiles over the clip."""
    x1 = np.percentile(c["x"], 5)
    y1 = np.percentile(c["y"], 5)
    x2 = np.percentile(np.array(c["x"]) + np.array(c["w"]), 95)
    y2 = np.percentile(np.array(c["y"]) + np.array(c["h"]), 95)
    fw, fh = np.median(c["w"]), np.median(c["h"])
    return Rect(int(x1 - 0.9 * fw), int(y1 - 0.7 * fh), int((x2 - x1) + 1.8 * fw), int((y2 - y1) + 2.2 * fh))


# ------------------------------------------------------------------ overlay border search
def _edge_maps(frames: list[np.ndarray]):
    import cv2
    gx = np.zeros((AH, AW), np.float32)
    gy = np.zeros((AH, AW), np.float32)
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx += np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        gy += np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    return gx / len(frames), gy / len(frames)


def _peaks(profile: np.ndarray, min_ratio: float = 2.0) -> np.ndarray:
    """Local-contrast score per line: a straight overlay border is a sharp peak vs. its neighbourhood."""
    n = len(profile)
    out = np.zeros(n)
    floor = np.percentile(profile, 60) + 1e-3
    for i in range(n):
        lo, hi = max(0, i - 16), min(n, i + 17)
        neigh = np.concatenate([profile[lo:max(lo, i - 3)], profile[min(hi, i + 4):hi]])
        if len(neigh) < 6:
            continue
        r = profile[i] / (np.median(neigh) + 1e-3)
        if r >= min_ratio and profile[i] >= floor:
            out[i] = r
    return out


def _find_overlay(frames, union: Rect, core: Rect) -> tuple[Rect, list[str]]:
    """Search outward from the people's FACES (core) for the overlay's persistent straight borders.
    Where no border exists in a direction, fall back to the generous person box (union) or frame edge."""
    gx, gy = _edge_maps(frames)
    notes = []
    y1, y2 = max(0, core.y), min(AH, core.y2)
    x1, x2 = max(0, core.x), min(AW, core.x2)
    colp = _peaks(gx[y1:max(y1 + 1, y2), :].mean(axis=0))    # vertical borders, measured along the faces' rows
    rowp = _peaks(gy[:, x1:max(x1 + 1, x2)].mean(axis=1))    # horizontal borders, along the faces' columns
    reach_x, reach_y = int(AW * 0.35), int(AH * 0.45)
    variant_x, variant_y = int(AW * 0.10), int(AH * 0.12)

    def nearest(peaks, start, stop, step, variant, ok=lambda i: True):
        """Nearest valid border; then also accept a second one shortly after (facecam resized mid-clip)."""
        first = None
        for i in range(start, stop, step):
            if peaks[i] > 0 and ok(i):
                first = i
                break
        if first is None:
            return None
        far = first
        for i in range(first + step * 4, first + step * variant, step):
            if 0 <= i < len(peaks) and peaks[i] > 0 and ok(i):
                far = i
        return far

    right = nearest(colp, min(AW - 1, x2 + 2), min(AW, x2 + reach_x), 1, variant_x)
    left = nearest(colp, max(0, x1 - 2), max(-1, x1 - reach_x), -1, variant_x)
    # horizontal borders only span the overlay's width: a line that continues beyond the overlay's side
    # borders is gameplay (a rooftop, a container edge), not the facecam frame
    Lx = 0 if left is None or left < AW * 0.08 else left
    Rx = AW if right is None or right > AW * 0.92 else right

    def is_overlay_row(i):
        inside = gy[max(0, i - 1):i + 2, Lx:Rx].max(axis=0).mean()
        outs = []
        if Rx < AW - 20:
            outs.append(gy[max(0, i - 1):i + 2, Rx + 6:min(AW, Rx + 6 + (Rx - Lx))].max(axis=0).mean())
        if Lx > 20:
            outs.append(gy[max(0, i - 1):i + 2, max(0, Lx - 6 - (Rx - Lx)):Lx - 6].max(axis=0).mean())
        return not outs or inside > 1.8 * max(outs)

    bottom = nearest(rowp, min(AH - 1, y2 + 2), min(AH, y2 + reach_y), 1, variant_y, is_overlay_row)
    top = nearest(rowp, max(0, y1 - 2), max(-1, y1 - reach_y), -1, variant_y, is_overlay_row)

    def resolve(found, fallback, frame_edge, name, near):
        span = AW if name in ("left", "right") else AH
        if found is not None and abs(found - frame_edge) < span * 0.08:
            # overlays don't leave a thin strip at the frame edge: that "border" is content (a chair, a shoulder)
            notes.append(f"{name} border {found} is next to the frame edge -> snapped to edge")
            return frame_edge
        if found is not None:
            notes.append(f"{name} border at {found}")
            return found
        if near:
            notes.append(f"{name} extends to frame edge")
            return frame_edge
        notes.append(f"{name}: no border, using person margin")
        return fallback
    L = resolve(left, max(0, union.x), 0, "left", union.x < AW * 0.2)
    R = resolve(right, min(AW, union.x2), AW, "right", union.x2 > AW * 0.8)
    T = resolve(top, max(0, union.y), 0, "top", union.y < AH * 0.2)
    B = resolve(bottom, min(AH, union.y2), AH, "bottom", union.y2 > AH * 0.75)
    return Rect(L, T, max(8, R - L), max(8, B - T)), notes


# ------------------------------------------------------------------ gameplay crop
def _static_ui_map(frames) -> np.ndarray:
    """Stream UI (viewer counters, logos, social bars) = strong edges that never change over the clip."""
    import cv2
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    gx, gy = _edge_maps(frames)
    e = gx + gy
    sd = g.std(axis=0)
    return (e > np.percentile(e, 90)) & (sd < np.percentile(sd, 25))


def _chat_rects(frames) -> list[Rect]:
    """Stream chat = a narrow column hugging the left/right frame edge whose tiles contain dense small
    text in nearly every frame AND keep changing (messages scroll). Game HUDs are mostly static, so they
    are not matched. Returned in analysis pixels."""
    import cv2
    if len(frames) < 6:
        return []
    T = 24
    ny, nx = AH // T, AW // T
    dens, chg = [], []
    prev = None
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        e = cv2.Canny(g, 80, 160) > 0
        dens.append(e[: ny * T, : nx * T].reshape(ny, T, nx, T).mean(axis=(1, 3)))
        if prev is not None:
            d = np.abs(g.astype(np.int16) - prev.astype(np.int16))[: ny * T, : nx * T]
            chg.append(d.reshape(ny, T, nx, T).mean(axis=(1, 3)))
        prev = g
    dens, chg = np.stack(dens), np.stack(chg)
    textlike = (dens > 0.06).mean(axis=0) >= 0.6           # (sparse) text present in most frames
    changing = (chg > 6).mean(axis=0) >= 0.35               # and it keeps changing
    cand = textlike & changing
    rects = []
    for side in ("left", "right"):
        lo, hi = (0, int(nx * 0.25)) if side == "left" else (int(nx * 0.75), nx)
        mask = np.zeros(cand.shape, np.uint8)
        mask[:, lo:hi] = cand[:, lo:hi]
        # bridge gaps between chat lines, then take the biggest connected block (ignore stray UI icons)
        mask = cv2.dilate(mask, np.ones((2, 2), np.uint8))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            continue
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x1, y1, w, h = (int(v) for v in stats[k, :4])
        x2, y2 = x1 + w, y1 + h
        touches_edge = (x1 <= 2) if side == "left" else (x2 >= nx - 2)
        tall_narrow = h >= ny * 0.12 and w <= nx * 0.25
        dense = (lab[y1:y2, x1:x2] == k).mean() >= 0.35
        if touches_edge and tall_narrow and dense:
            rects.append(Rect(int(x1 * T), int(y1 * T), int(w * T), int(h * T)))
    return rects


def _motion_center(frames, exclude: Rect | None) -> float:
    if len(frames) < 3:
        return AW / 2
    g = np.stack([f.mean(axis=2) for f in frames]).astype(np.float32)
    m = np.abs(np.diff(g, axis=0)).mean(axis=0)
    if exclude:
        m[exclude.y:exclude.y2, exclude.x:exclude.x2] = 0
    col = m.sum(axis=0)
    return float((col * np.arange(AW)).sum() / col.sum()) if col.sum() > 0 else AW / 2


def _gameplay_crop(W, H, aspect: float, fc: Rect | None, motion_cx: float,
                   ui: np.ndarray | None = None) -> tuple[Rect, Rect | None, str]:
    """Crop with the given aspect that avoids the facecam (hard) and stream UI clutter (soft);
    blur-mask fallback if the facecam cannot be avoided. Static UI bars at the top/bottom are trimmed."""
    if ui is not None:
        sx_ui, sy_ui = AW / W, AH / H
        col_ui = ui.sum(axis=0).astype(np.float32)
        top, bot = 0, H
        rows = ui.mean(axis=1)
        for i in range(int(AH * 0.10)):                 # trim static bars hugging the top/bottom edge
            if rows[i] > 0.08:
                top = int((i + 2) / sy_ui)
        for i in range(AH - 1, int(AH * 0.90), -1):
            if rows[i] > 0.08:
                bot = int((i - 1) / sy_ui)
        if bot - top >= H * 0.85:
            H0, Y0 = bot - top, top
        else:
            H0, Y0 = H, 0
    else:
        col_ui, H0, Y0, sx_ui = None, H, 0, 1
    cands = []
    full_w = int(H0 * aspect)
    if full_w <= W:
        # full-height crops: slide horizontally; prefer action, avoid static UI clutter
        for x in range(0, W - full_w + 1, max(2, W // 96)):
            r = Rect(x, Y0, full_w, H0)
            ov = r.inter(fc) if fc else 0
            ui_pen = 0.0
            if col_ui is not None:
                ui_pen = float(col_ui[int(x * sx_ui):int((x + full_w) * sx_ui)].sum()) / max(1.0, col_ui.sum())
            cands.append((ov, -abs(x + full_w / 2 - motion_cx) / W - 1.5 * ui_pen, r))
    else:
        h = int(W / aspect)
        for y in range(0, H - h + 1, max(2, H // 54)):
            r = Rect(0, y, W, h)
            ov = r.inter(fc) if fc else 0
            cands.append((ov, -abs(y + h / 2 - H / 2) / H, r))
    if fc:
        # crops above / below the facecam (shorter, but clean)
        for top, bot in ((0, fc.y), (fc.y2, H)):
            hh = bot - top
            if hh >= H * 0.55:
                ww = int(hh * aspect)
                if ww <= W:
                    x = int(np.clip(motion_cx - ww / 2, 0, W - ww))
                    cands.append((0, -abs(x + ww / 2 - motion_cx) / W - (H - hh) / H, Rect(x, top, ww, hh)))
    cands.sort(key=lambda c: (c[0], -c[1]))
    ov, _, best = cands[0]
    if ov == 0:
        return best, None, "gameplay crop avoids facecam"
    m = Rect(max(fc.x, best.x) - best.x, max(fc.y, best.y) - best.y,
             min(fc.x2, best.x2) - max(fc.x, best.x), min(fc.y2, best.y2) - max(fc.y, best.y))
    return best, m, "facecam overlaps every gameplay crop -> overlapping area blurred"


# ------------------------------------------------------------------ main entry
def analyze(path: Path, start: float, end: float, src_w: int, src_h: int, samples: int = 28) -> LayoutPlan:
    times = [start + (end - start) * (k + 0.5) / samples for k in range(samples)]
    frames = grab_frames(path, times)
    sx, sy = src_w / AW, src_h / AH
    if len(frames) < 4:
        return _gameplay_only(src_w, src_h, frames, ["too few frames decoded"])
    faces = [detect_faces(f) for f in frames]
    people = _cluster_people(faces, len(frames))
    if not people:
        return _gameplay_only(src_w, src_h, frames, ["no persistent real faces -> gameplay focus"])
    from ..common import cfg
    mode = cfg()["editing"].get("layout_mode", "auto")
    others = []
    if mode != "camera":
        people, others = _group_with_anchor(people)

    boxes = [_person_box(c) for c in people]
    ux1 = max(0, min(b.x for b in boxes))
    uy1 = max(0, min(b.y for b in boxes))
    ux2 = min(AW, max(b.x2 for b in boxes))
    uy2 = min(AH, max(b.y2 for b in boxes))
    union = Rect(ux1, uy1, ux2 - ux1, uy2 - uy1)
    # core = where the faces actually are over the clip (robust percentiles)
    cx1 = min(np.percentile(c["x"], 5) for c in people)
    cy1 = min(np.percentile(c["y"], 5) for c in people)
    cx2 = max(np.percentile(np.array(c["x"]) + np.array(c["w"]), 95) for c in people)
    cy2 = max(np.percentile(np.array(c["y"]) + np.array(c["h"]), 95) for c in people)
    core = Rect(int(cx1), int(cy1), int(cx2 - cx1), int(cy2 - cy1))
    overlay, notes = _find_overlay(frames, union, core)
    # never smaller than the faces themselves (+ a small margin so nobody's head touches the edge)
    m = int(0.25 * np.median([np.median(c["w"]) for c in people]))
    ox1, oy1 = min(overlay.x, max(0, core.x - m)), min(overlay.y, max(0, core.y - m))
    ox2, oy2 = max(overlay.x2, min(AW, core.x2 + m)), max(overlay.y2, min(AH, core.y2 + m))
    overlay = Rect(ox1, oy1, ox2 - ox1, oy2 - oy1)
    # anyone else whose face sits inside the found overlay is part of the facecam too
    for c in others:
        if overlay.x <= np.median(c["cx"]) <= overlay.x2 and overlay.y <= np.median(c["cy"]) <= overlay.y2:
            people.append(c)
    if others:
        notes.append(f"{len(others)} on-screen face(s) outside the facecam ignored (game/video content)")
    area_frac = overlay.w * overlay.h / (AW * AH)
    person_faces = [Rect(int(np.median(c["x"])), int(np.median(c["y"])), int(np.median(c["w"])), int(np.median(c["h"])))
                    for c in people]

    if area_frac > 0.55 or mode == "camera":
        # people fill the frame: it's a full camera shot (IRL / just chatting), not an overlay
        plan = LayoutPlan("camera", src_w, src_h, people=len(people),
                          person_boxes=[asdict(b.scaled(sx, sy)) for b in person_faces], notes=notes + ["full-frame camera"])
        plan.facecam = union.scaled(sx, sy).even(src_w, src_h)
        return _finish_camera(plan)

    fc = overlay.scaled(sx, sy).even(src_w, src_h)
    plan = LayoutPlan("facecam_stack", src_w, src_h, facecam=fc, people=len(people),
                      person_boxes=[asdict(b.scaled(sx, sy)) for b in person_faces], notes=notes)
    # band height from the facecam's own aspect (fit, never crop people), clamped to 25-35% of the frame
    plan.fc_band_h = int(np.clip(OUT_W * fc.h / max(1, fc.w), FC_MIN_H, FC_MAX_H)) // 2 * 2
    gp_h = OUT_H - plan.fc_band_h
    mcx = _motion_center(frames, overlay) * sx
    plan.gameplay, plan.gameplay_mask, why = _gameplay_crop(src_w, src_h, OUT_W / gp_h, fc, mcx, _static_ui_map(frames))
    plan.gameplay = plan.gameplay.even(src_w, src_h)
    plan.notes.append(why)
    g = plan.gameplay
    for r in _chat_rects(frames):
        rs = r.scaled(sx, sy)
        ix1, iy1 = max(rs.x, g.x), max(rs.y, g.y)
        ix2, iy2 = min(rs.x2, g.x2), min(rs.y2, g.y2)
        if ix2 - ix1 > 16 and iy2 - iy1 > 16:
            plan.blur_rects.append(asdict(Rect(ix1 - g.x, iy1 - g.y, ix2 - ix1, iy2 - iy1)))
    if plan.blur_rects:
        plan.notes.append(f"stream chat inside gameplay crop blurred ({len(plan.blur_rects)} area)")
    plan.caption_y = plan.fc_band_h + int(gp_h * 0.32)   # below the seam phrase
    plan.hook_y = _hook_y(plan)
    log.info("Layout: facecam %s with %d person(s), band %dpx, gameplay crop %s", (fc.x, fc.y, fc.w, fc.h),
             plan.people, plan.fc_band_h, (plan.gameplay.x, plan.gameplay.y, plan.gameplay.w, plan.gameplay.h))
    return plan


def fc_fit(plan: LayoutPlan) -> tuple[int, int, int, int]:
    """Where the facecam lands inside its band (contain-fit, centered): (x, y, w, h)."""
    fc = plan.facecam
    s = min(OUT_W / fc.w, plan.fc_band_h / fc.h)
    w, h = int(fc.w * s) // 2 * 2, int(fc.h * s) // 2 * 2
    return (OUT_W - w) // 2, (plan.fc_band_h - h) // 2, w, h


def faces_in_output(plan: LayoutPlan) -> list[Rect]:
    """Expected face positions in the output frame (for caption/hook placement)."""
    if plan.kind != "facecam_stack":
        return []
    ox, oy, w, h = fc_fit(plan)
    s = w / plan.facecam.w
    out = []
    for b in plan.person_boxes:
        out.append(Rect(int(ox + (b["x"] - plan.facecam.x) * s), int(oy + (b["y"] - plan.facecam.y) * s),
                        int(b["w"] * s), int(b["h"] * s)))
    return out


def _hook_y(plan: LayoutPlan) -> int:
    """Seam phrase: sits just below the line where facecam and gameplay meet (top-anchored there),
    so it never covers a face and reads as the clip's 'headline'."""
    if plan.kind == "facecam_stack":
        return plan.fc_band_h + 22
    return 90


def _gameplay_only(W, H, frames, notes) -> LayoutPlan:
    plan = LayoutPlan("gameplay_focus", W, H, notes=notes)
    asp = 1.0
    gh = int(OUT_W / asp)
    mcx = _motion_center(frames, None) * (W / AW) if frames else W / 2
    cw = min(W, int(H * asp)) // 2 * 2
    x = int(np.clip(mcx - cw / 2, 0, W - cw)) // 2 * 2
    plan.gameplay = Rect(x, 0, cw, min(H, int(cw / asp)) // 2 * 2)
    plan.caption_y = 400 + gh + 170
    plan.hook_y = 250
    return plan


def _finish_camera(plan: LayoutPlan) -> LayoutPlan:
    """Full-frame camera: one static 9:16 crop containing every person (no per-face tracking)."""
    W, H = plan.src_w, plan.src_h
    u = plan.facecam
    cw = min(W, int(H * 9 / 16)) // 2 * 2
    if u.w > cw:  # group wider than a 9:16 slice -> show the whole width in a stacked frame instead
        plan.notes.append("group wider than 9:16 crop -> full-width fit with blurred fill")
        plan.gameplay = Rect(0, 0, W, H)
        plan.kind = "camera_fit"
    else:
        x = int(np.clip(u.x + u.w / 2 - cw / 2, 0, W - cw)) // 2 * 2
        plan.gameplay = Rect(x, 0, cw, H)
    plan.facecam = None
    plan.caption_y = int(OUT_H * 0.70)
    plan.hook_y = 220
    return plan
