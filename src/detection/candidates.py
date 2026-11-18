"""Turn the interest curve into candidate moments with complete setup + payoff windows."""
from __future__ import annotations

import numpy as np

from ..common import cfg, get_logger

log = get_logger("candidates")


def _sentence_start_before(transcript: dict, t: float, floor: float) -> float:
    """Start of the sentence/segment that contains t (never earlier than floor)."""
    best = t
    for s in transcript["segments"]:
        if s["s"] <= t <= s["e"] + 0.3:
            best = s["s"]
            break
        if s["s"] > t:
            break
    return max(floor, best - 0.25)


def _sentence_end_after(transcript: dict, t: float, ceil: float) -> float:
    for s in transcript["segments"]:
        if s["s"] - 0.3 <= t <= s["e"]:
            return min(ceil, s["e"] + 0.6)
        if s["s"] > t:
            break
    return min(ceil, t)


def find(transcript: dict, sig: dict, duration: float) -> list[dict]:
    c = cfg()["detection"]
    interest = sig["interest"]
    n = len(interest)
    if n < 10:
        return []
    thr = max(np.percentile(interest, 80), interest.mean() + 0.5 * interest.std())
    mind = c["peak_min_distance_s"]
    order = np.argsort(-interest)
    peaks: list[int] = []
    for i in order:
        if interest[i] < thr or len(peaks) >= c["max_candidates"]:
            break
        if all(abs(i - p) >= mind for p in peaks):
            peaks.append(int(i))

    cands = []
    for p in sorted(peaks):
        s0 = max(0.0, p - c["setup_before_peak_s"])
        start = _sentence_start_before(transcript, s0, max(0.0, p - 35))
        e0 = min(duration, p + c["payoff_after_peak_s"])
        end = _sentence_end_after(transcript, e0, min(duration, p + 22))
        if end - start < c["min_clip_s"]:
            end = min(duration, start + c["min_clip_s"])
        if end - start > c["max_clip_s"]:
            start = end - c["max_clip_s"]
        seg = slice(int(start), max(int(start) + 1, int(end)))
        cands.append({
            "peak": float(p), "start": round(float(start), 2), "end": round(float(end), 2),
            "signal_score": round(float(interest[p]), 3),
            "signals": {
                "loudness_peak_z": round(float(sig["loud_z"][seg].max()), 2),
                "visual_peak_z": round(float(sig["vis_z"][seg].max()), 2),
                "hype_words": int(sig["hype"][seg].sum()),
                "laughs": int(sig["laugh"][seg].sum()),
                "pause_then_burst": bool(sig["burst"][seg].any()),
            }})
    log.info("Found %d raw candidates (threshold %.2f)", len(cands), thr)
    return cands
