"""AI review + 0-100 virality scoring, rejection rules, dedup, learned weights."""
from __future__ import annotations

import json

import numpy as np

from .. import db
from ..common import ask_claude_json, cfg, claude_available, get_logger, profile
from ..transcription.transcribe import text_between

log = get_logger("review")


def categories() -> list[str]:
    return list(profile()["categories"].keys())


def weights() -> dict:
    return profile()["scoring"]["weights"]


PROMPT = """You are a senior short-form video editor choosing {content_type} moments for YouTube Shorts
and Instagram Reels aimed at {regions}. Quality over quantity: reject anything boring.

Content: {content_label}. Streamer: {streamer}.
Editorial brief: {brief}
Moment categories (key: meaning):
{cat_desc}
You only see the transcript and measured signals (loudness/visual spikes); you cannot see the video,
so never invent visual details that the transcript and signals do not support.

For EACH candidate below decide if it is a genuinely entertaining, scroll-stopping moment.
Reject if: no payoff, starts mid-conversation with confusing context, lots of dead air,
unintelligible audio, repetitive, or would only work with misleading framing.
You may tighten or extend start/end (seconds relative to the candidate's context window start,
which is 0.0 in its transcript) so the clip begins at a natural sentence start right before the
setup and ends right after the payoff/reaction. Keep 12-60 seconds.

Return ONLY a JSON array, one object per candidate:
{{"id": <id>, "keep": true/false, "reject_reason": "<if rejected>",
  "start": <sec rel. to window>, "end": <sec rel. to window>,
  "category": one of {cats},
  "summary": "<what happens, factual, from the transcript>",
  "why": "<why a Western gaming audience would watch/share it>",
  "hook": "<what happens in the first 1-2 seconds that hooks viewers>",
  "on_screen_hook": "<2-6 word catchy phrase in {language_style} shown between facecam and gameplay; double meaning welcome; no emoji; never misleading>",
  "complete_arc": true/false,
  "scores": {{"hook":0-10,"entertainment":0-10,"unexpectedness":0-10,"emotion":0-10,
             "relatability":0-10,"story_clarity":0-10,"replay":0-10,"standalone":0-10}}}}

CANDIDATES:
{blocks}
"""


def _local_quality(transcript: dict, sig: dict, start: float, end: float) -> tuple[float, float]:
    segs = [s for s in transcript["segments"] if s["e"] >= start and s["s"] <= end]
    if segs:
        lp = np.mean([s["logprob"] for s in segs])
        ns = np.mean([s["no_speech"] for s in segs])
        audio_q = float(np.clip(10 + lp * 8 - ns * 4, 0, 10))  # logprob ~ -0.2 good, -1 bad
    else:
        audio_q = 5.0  # no speech: gameplay-only audio, neutral
    loud = sig["loud"][int(start):int(end) + 1]
    if len(loud) and (loud > -1.0).mean() > 0.05:
        audio_q = max(0.0, audio_q - 3)  # clipping / distortion
    vis = sig["change"][int(start):int(end) + 1]
    visual_q = float(np.clip(4 + 6 * (vis > np.percentile(sig["change"], 30)).mean(), 0, 10)) if len(vis) else 5.0
    return round(audio_q, 1), round(visual_q, 1)


def _learned_factor(category: str) -> float:
    r = db.one("SELECT value FROM learned_weights WHERE key=?", (f"{profile()['id']}:category:{category}",))
    return float(r["value"]) if r else 1.0


def virality(scores: dict, category: str) -> float:
    w = weights()
    total = sum(w[k] * float(scores.get(k, 0)) / 10 for k in w) * 100 / sum(w.values())
    return round(float(np.clip(total * _learned_factor(category), 0, 100)), 1)


def _heuristic(c: dict) -> dict:
    s = c["signals"]
    base = min(10, 3 + c["signal_score"] * 2.5)
    return {"keep": True, "start": None, "end": None, "category": "other",
            "summary": "Auto-detected high-energy moment (no AI review).",
            "why": f"Loudness spike z={s['loudness_peak_z']}, visual z={s['visual_peak_z']}",
            "hook": "", "on_screen_hook": "", "complete_arc": True,
            "scores": {k: base for k in ["hook", "entertainment", "unexpectedness", "emotion",
                                          "relatability", "story_clarity", "replay", "standalone"]}}


def review(source: dict, transcript: dict, sig: dict, cands: list[dict]) -> list[dict]:
    rc = cfg()["review"]
    use_llm = rc["use_llm"] and claude_available()
    if not use_llm:
        log.warning("AI review disabled/unavailable - using signal-only heuristic scoring (weaker).")
    ctx_pre, ctx_post = 25, 15
    for i, c in enumerate(cands):
        c["id"] = i
        c["win0"] = max(0.0, c["start"] - ctx_pre)
        c["win1"] = min(source["duration"], c["end"] + ctx_post)

    results = {}
    if use_llm:
        bs = rc["batch_size"]
        for b in range(0, len(cands), bs):
            batch = cands[b:b + bs]
            blocks = []
            for c in batch:
                blocks.append(
                    f"--- id={c['id']} | proposed start={c['start'] - c['win0']:.1f} end={c['end'] - c['win0']:.1f}"
                    f" | signals={json.dumps(c['signals'])}\n"
                    f"{text_between(transcript, c['win0'], c['win1']) or '(no speech - gameplay audio only)'}")
            pr = profile()
            game = source.get("game") or pr.get("game")
            prompt = PROMPT.format(
                content_type=pr["content_type"], regions=", ".join(pr["audience"]["regions"]),
                content_label=f"{game} {pr['content_type']}" if game else pr["content_type"],
                streamer=source.get("streamer") or "unknown", brief=pr["scoring"]["editor_brief"],
                cat_desc="\n".join(f"- {k}: {v}" for k, v in pr["categories"].items()),
                language_style=pr["audience"]["language_style"],
                cats=json.dumps(categories()), blocks="\n\n".join(blocks))
            log.info("AI reviewing candidates %d-%d of %d", b + 1, b + len(batch), len(cands))
            try:
                out = ask_claude_json(prompt)
                for o in out if isinstance(out, list) else []:
                    results[int(o["id"])] = o
            except Exception as e:
                log.error("AI review failed for batch, using heuristic: %s", e)

    reviewed = []
    for c in cands:
        r = results.get(c["id"]) or _heuristic(c)
        st = c["win0"] + float(r["start"]) if r.get("start") is not None else c["start"]
        en = c["win0"] + float(r["end"]) if r.get("end") is not None else c["end"]
        st, en = max(0.0, st), min(source["duration"], en)
        if en - st < cfg()["detection"]["min_clip_s"] or en - st > 75:
            st, en = c["start"], c["end"]
        aq, vq = _local_quality(transcript, sig, st, en)
        scores = {**{k: float(v) for k, v in (r.get("scores") or {}).items()},
                  "audio_quality": aq, "visual_quality": vq}
        cat = r.get("category") if r.get("category") in categories() else "other"
        v = virality(scores, cat)
        reason = r.get("reject_reason") or ""
        keep = bool(r.get("keep"))
        if keep and v < rc["min_virality"]:
            keep, reason = False, f"score {v} below {rc['min_virality']}"
        if keep and scores.get("story_clarity", 10) < 4:
            keep, reason = False, "confusing context"
        if keep and aq < 3:
            keep, reason = False, "poor/unintelligible audio"
        if keep and r.get("complete_arc") is False:
            keep, reason = False, "no clear beginning/ending"
        reviewed.append({**c, "start": round(st, 2), "end": round(en, 2), "virality": v, "keep": keep,
                         "reject_reason": reason, "category": cat, "summary": r.get("summary", ""),
                         "why": r.get("why", ""), "hook": r.get("hook", ""),
                         "on_screen_hook": r.get("on_screen_hook", ""), "scores": scores,
                         "ai_reviewed": c["id"] in results})

    # dedup overlapping moments (keep the higher score)
    reviewed.sort(key=lambda x: x["virality"], reverse=True)
    kept = []
    for r in reviewed:
        if not r["keep"]:
            continue
        dup = False
        for k in kept:
            ov = min(r["end"], k["end"]) - max(r["start"], k["start"])
            if ov > rc["overlap_threshold"] * min(r["end"] - r["start"], k["end"] - k["start"]):
                dup = True
                break
        if dup:
            r["keep"], r["reject_reason"] = False, "duplicate/overlapping moment"
        elif len(kept) >= rc["max_final_clips"]:
            r["keep"], r["reject_reason"] = False, "over max_final_clips"
        else:
            kept.append(r)

    with db.connect() as con:
        # candidates still referenced by a clip are kept (marked superseded) to preserve clip history
        con.execute("UPDATE candidates SET status='superseded' WHERE source_id=? AND status IN "
                    "('candidate','rejected','selected') AND id IN (SELECT candidate_id FROM clips "
                    "WHERE candidate_id IS NOT NULL)", (source["id"],))
        con.execute("DELETE FROM candidates WHERE source_id=? AND status IN ('candidate','rejected','selected')",
                    (source["id"],))
        for r in reviewed:
            cur = con.execute(
                "INSERT INTO candidates (source_id,start,end,peak,signal_score,virality,category,summary,why,hook,"
                "status,reject_reason,review_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source["id"], r["start"], r["end"], r["peak"], r["signal_score"], r["virality"], r["category"],
                 r["summary"], r["why"], r["hook"], "selected" if r["keep"] else "rejected", r["reject_reason"],
                 db.dumps({k: r[k] for k in ("scores", "signals", "on_screen_hook", "ai_reviewed")}),
                 db.now_iso()))
            r["db_id"] = cur.lastrowid
    log.info("Review: %d kept / %d rejected", len(kept), len(reviewed) - len(kept))
    return kept
