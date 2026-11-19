"""Automatic quality control for rendered clips - measured on real frames and audio, not AI opinions.

Every check returns pass / warn / fail with the measured value. A clip with any FAIL is not eligible
for publishing; render_with_qc() automatically re-renders with a corrected layout and re-checks.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ..common import get_logger, probe
from .layout import AH, AW, OUT_H, OUT_W, LayoutPlan, Rect, detect_faces, faces_in_output, fc_fit, grab_frames

log = get_logger("qc")


def _ff(args: list, text=True) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", *args], capture_output=True, text=text, errors="ignore")


def _check(name, status, detail, value=None):
    return {"name": name, "status": status, "detail": detail, "value": value}


def _out_to_source(t: float, segs: list) -> float | None:
    acc = 0.0
    for a, b in segs:
        if t <= acc + (b - a):
            return a + (t - acc)
        acc += b - a
    return None


def _plan_from(d: dict) -> LayoutPlan:
    p = LayoutPlan(**{k: v for k, v in d.items() if k not in ("facecam", "gameplay", "gameplay_mask")})
    p.blur_rects = d.get("blur_rects") or []
    for k in ("facecam", "gameplay", "gameplay_mask"):
        setattr(p, k, Rect(**d[k]) if d.get(k) else None)
    return p


def run_qc(out_path: str, res: dict, source_path: str, expected_words: int) -> dict:
    checks = []
    out = Path(out_path)
    plan = _plan_from(res["plan"])

    # ---------------- container / streams
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,duration,start_time,r_frame_rate",
                        "-of", "json", str(out)], capture_output=True, text=True)
    st = json.loads(r.stdout or "{}").get("streams", [])
    v = next((s for s in st if s["codec_type"] == "video"), {})
    a = next((s for s in st if s["codec_type"] == "audio"), {})
    ok_fmt = (v.get("codec_name") == "h264" and v.get("width") == OUT_W and v.get("height") == OUT_H
              and v.get("pix_fmt") == "yuv420p" and a.get("codec_name") == "aac" and a.get("sample_rate") == "48000")
    checks.append(_check("format", "pass" if ok_fmt else "fail",
                         f"{v.get('codec_name')} {v.get('width')}x{v.get('height')} {v.get('pix_fmt')}, "
                         f"{a.get('codec_name')} {a.get('sample_rate')} Hz"))
    vd, ad = float(v.get("duration") or 0), float(a.get("duration") or 0)
    drift = abs(vd - ad)
    starts = abs(float(v.get("start_time") or 0) - float(a.get("start_time") or 0))
    checks.append(_check("av_sync", "pass" if drift < 0.1 and starts < 0.05 else "fail",
                         f"video {vd:.2f}s vs audio {ad:.2f}s (drift {drift * 1000:.0f} ms, start offset {starts * 1000:.0f} ms)",
                         round(drift, 3)))
    exp = res.get("cut_duration") or vd
    checks.append(_check("duration", "pass" if abs(vd - exp) < 0.3 else "fail", f"{vd:.2f}s rendered vs {exp:.2f}s planned"))

    # ---------------- decode errors / black / freeze (one pass)
    r = _ff(["-v", "error", "-i", str(out), "-vf", "blackdetect=d=0.25:pix_th=0.08,freezedetect=n=-55dB:d=2.5",
             "-af", "silencedetect=n=-45dB:d=1.5,ebur128=peak=true", "-f", "null", "-"])
    r_info = _ff(["-i", str(out), "-vf", "blackdetect=d=0.25:pix_th=0.08,freezedetect=n=-55dB:d=2.5",
                  "-af", "silencedetect=n=-45dB:d=1.5,ebur128=peak=true", "-f", "null", "-"])
    errs = [l for l in r.stderr.splitlines() if l.strip() and "Last message repeated" not in l]
    checks.append(_check("decode", "pass" if not errs else "fail",
                         "clean decode" if not errs else f"{len(errs)} decoder error(s): {errs[0][:120]}"))
    log_txt = r_info.stderr
    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log_txt)]
    # the 0.15s fade-in / 0.25s fade-out are intentional
    mid_black = sum(e - s for s, e in blacks if s > 0.4 and e < vd - 0.4)
    checks.append(_check("black_frames", "pass" if mid_black < 0.3 else "fail",
                         f"{mid_black:.2f}s of black inside the clip", round(mid_black, 2)))
    freezes = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", log_txt)]
    checks.append(_check("frozen_video", "pass" if not freezes else "warn",
                         "no frozen stretches" if not freezes else f"frozen for {max(freezes):.1f}s (static source shot?)",
                         max(freezes) if freezes else 0))
    m_i = re.findall(r"I:\s+(-?[\d.]+) LUFS", log_txt)
    m_tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log_txt)
    lufs = float(m_i[-1]) if m_i else None
    tp = float(m_tp[-1]) if m_tp else None
    checks.append(_check("loudness", "pass" if lufs is not None and -16.5 <= lufs <= -11.5 else "fail",
                         f"integrated {lufs} LUFS (target -14)", lufs))
    checks.append(_check("true_peak", "pass" if tp is not None and tp <= -0.5 else "warn",
                         f"true peak {tp} dBFS", tp))
    sil = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", log_txt)]
    checks.append(_check("dead_air", "pass" if not sil else "warn",
                         "no long silences" if not sil else f"{len(sil)} silent stretch(es), longest {max(sil):.1f}s"))

    # ---------------- captions
    if expected_words > 0:
        checks.append(_check("captions", "pass" if res.get("captions", 0) > 0 else "fail",
                             f"{res.get('captions', 0)} caption words synced (transcript has {expected_words})"))
    else:
        checks.append(_check("captions", "pass", "no speech in this moment - captions not needed"))
    faces_out = faces_in_output(plan)
    cap = Rect(60, plan.caption_y - 70, OUT_W - 120, 140)
    hits = [f for f in faces_out if cap.inter(f) > 0]
    if plan.kind == "facecam_stack" and plan.caption_y - 70 < plan.hook_y + 205:
        hits = hits + ["seam phrase"]
    checks.append(_check("caption_position", "pass" if not hits else "fail",
                         f"captions at y={plan.caption_y}" + (" overlap a face" if hits else ", clear of faces")))

    # ---------------- composition
    if plan.kind == "facecam_stack":
        frac = plan.fc_band_h / OUT_H
        checks.append(_check("composition", "pass" if 0.24 <= frac <= 0.46 else "warn",
                             f"facecam {frac:.0%} / gameplay {1 - frac:.0%} of the frame", round(frac, 3)))
        fc, g = plan.facecam, plan.gameplay
        overlap = g.inter(fc) - (plan.gameplay_mask.w * plan.gameplay_mask.h if plan.gameplay_mask else 0)
        checks.append(_check("duplicate_facecam", "pass" if overlap <= 0 else "fail",
                             "gameplay crop excludes the facecam" if not plan.gameplay_mask else
                             "facecam overlap inside gameplay is blurred", overlap))
        checks.append(_check("layout_consistency", "pass",
                             "one fixed layout for the whole clip (no switching, no per-face tracking)"))
        checks += _people_checks(out, res, source_path, plan)
    else:
        checks.append(_check("composition", "pass", f"{plan.kind} layout"))
        checks.append(_check("layout_consistency", "pass", "one fixed crop for the whole clip"))

    worst = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    return {"status": worst, "checks": checks}


def _people_checks(out: Path, res: dict, source_path: str, plan: LayoutPlan) -> list[dict]:
    """Compare faces in the source facecam vs. the rendered facecam band on frames across the clip."""
    segs = res["segments"]
    dur = res.get("cut_duration") or 10
    t_out = [dur * (k + 0.5) / 8 for k in range(8)]
    t_src = [_out_to_source(t, segs) for t in t_out]
    pairs = [(to, ts) for to, ts in zip(t_out, t_src) if ts is not None]
    src_frames = grab_frames(Path(source_path), [ts for _, ts in pairs], AW, AH)
    out_frames = grab_frames(out, [to for to, _ in pairs], OUT_W // 2, OUT_H // 2)
    sx, sy = plan.src_w / AW, plan.src_h / AH
    fc = plan.facecam
    ox, oy, fw, fh = fc_fit(plan)
    band = Rect(ox // 2, oy // 2, fw // 2, fh // 2)  # fitted facecam inside the half-res output frame
    missing, cut, samples = 0, 0, 0
    detail_rows = []
    for sf, of in zip(src_frames, out_frames):
        samples += 1
        sfaces = [f for f in detect_faces(sf) if f[4] >= 0.7]
        in_fc, partial = 0, 0
        for (x, y, w, h, _) in sfaces:
            b = Rect(int(x * sx), int(y * sy), int(w * sx), int(h * sy))
            inside = b.inter(fc) / max(1, b.w * b.h)
            if inside >= 0.85:
                in_fc += 1
            elif inside >= 0.15:
                partial += 1  # a face straddling the facecam crop edge = someone cut off
        ofaces = [f for f in detect_faces(of) if f[4] >= 0.6]
        o_in = sum(1 for (x, y, w, h, _) in ofaces
                   if band.x <= x + w / 2 <= band.x2 and band.y <= y + h / 2 <= band.y2)
        if o_in < in_fc:
            missing += 1
        if partial:
            cut += 1
        detail_rows.append(f"{in_fc}->{o_in}")
    ok_rate = 1 - missing / max(1, samples)
    return [
        _check("participants_visible", "pass" if ok_rate >= 0.8 else "fail",
               f"{plan.people} person(s) in facecam; faces source->output per sample: {', '.join(detail_rows)}",
               round(ok_rate, 2)),
        _check("nobody_cut_off", "pass" if cut <= 1 else "fail",
               "no face crosses the facecam crop edge" if not cut else f"a face crosses the crop edge in {cut}/{samples} samples",
               cut),
    ]


def expand_plan(plan: LayoutPlan, frac: float = 0.10) -> LayoutPlan:
    """Grow the facecam crop (to include someone at the edge) and re-pick a non-overlapping gameplay crop."""
    from .layout import FC_MAX_H, FC_MIN_H, _gameplay_crop
    fc = plan.facecam
    dx, dy = int(fc.w * frac), int(fc.h * frac)
    nfc = Rect(max(0, fc.x - dx), max(0, fc.y - dy), 0, 0)
    nfc.w = min(plan.src_w, fc.x2 + dx) - nfc.x
    nfc.h = min(plan.src_h, fc.y2 + dy) - nfc.y
    plan.facecam = nfc.even(plan.src_w, plan.src_h)
    plan.fc_band_h = int(np.clip(OUT_W * plan.facecam.h / plan.facecam.w, FC_MIN_H, FC_MAX_H)) // 2 * 2
    gp_h = OUT_H - plan.fc_band_h
    plan.gameplay, plan.gameplay_mask, why = _gameplay_crop(plan.src_w, plan.src_h, OUT_W / gp_h, plan.facecam,
                                                            plan.gameplay.x + plan.gameplay.w / 2)
    plan.gameplay = plan.gameplay.even(plan.src_w, plan.src_h)
    plan.caption_y = plan.fc_band_h + int(gp_h * 0.32)
    plan.hook_y = plan.fc_band_h + 22
    plan.notes.append(f"QC retry: facecam expanded {frac:.0%}; {why}")
    return plan


def render_with_qc(source, transcript, sig, cand, clip_id, corrections=None, hook_text="", max_retries=2) -> dict:
    """Render, measure, and automatically re-render with a corrected layout until QC passes (or retries run out)."""
    from ..transcription.transcribe import words_between
    from .render import render
    expected = len(words_between(transcript, cand["start"], cand["end"]))
    override = None
    attempts = []
    for attempt in range(max_retries + 1):
        res = render(source, transcript, sig, cand, clip_id, corrections=corrections, hook_text=hook_text,
                     layout_override=override)
        qc = run_qc(res["path"], res, source["local_path"], expected)
        failed = [c["name"] for c in qc["checks"] if c["status"] == "fail"]
        attempts.append({"attempt": attempt + 1, "status": qc["status"], "failed": failed})
        log.info("QC %s attempt %d: %s%s", clip_id, attempt + 1, qc["status"].upper(),
                 f" ({', '.join(failed)})" if failed else "")
        if qc["status"] != "fail" or attempt == max_retries:
            break
        plan = _plan_from(res["plan"])
        if plan.kind == "facecam_stack" and {"participants_visible", "nobody_cut_off", "duplicate_facecam"} & set(failed):
            override = expand_plan(plan, 0.10 * (attempt + 1))
        elif "caption_position" in failed:
            plan.caption_y = min(OUT_H - 200, plan.caption_y + 160)
            override = plan
        else:
            override = plan  # technical failure (encode/sync): plain re-render
    res["qc"] = {**qc, "attempts": attempts}
    return res
