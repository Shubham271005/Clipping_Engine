"""Word-level transcription of the full stream with faster-whisper (GPU when available)."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from ..common import DIRS, cfg, decode_audio, get_logger, probe, setup_cuda_dlls

log = get_logger("transcribe")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _load_model():
    setup_cuda_dlls()
    from faster_whisper import WhisperModel
    c = cfg()["transcription"]
    if c["device"] in ("auto", "cuda"):
        try:
            m = WhisperModel(c["model"], device="cuda", compute_type="float16")
            log.info("Whisper %s on CUDA", c["model"])
            return m
        except Exception as e:
            if c["device"] == "cuda":
                raise
            log.warning("CUDA unavailable (%s) - falling back to CPU", str(e)[:120])
    log.info("Whisper %s on CPU (int8)", c["cpu_model"])
    return WhisperModel(c["cpu_model"], device="cpu", compute_type="int8")


def transcribe(source: dict, force: bool = False) -> dict:
    out = DIRS["transcripts"] / f"{source['id']}.json"
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))
    path = Path(source["local_path"])
    info = probe(path)
    result = {"source_id": source["id"], "language": cfg()["transcription"]["language"], "segments": []}
    if not info["has_audio"]:
        out.write_text(json.dumps(result), encoding="utf-8")
        return result

    model = _load_model()
    chunk = cfg()["transcription"]["chunk_minutes"] * 60
    n = max(1, math.ceil(info["duration"] / chunk))
    for i in range(n):
        off = i * chunk
        audio = decode_audio(path, 16000, off, chunk)
        if audio.size < 16000:
            continue
        log.info("Transcribing chunk %d/%d (%s min)", i + 1, n, int(off // 60))
        segs, _ = model.transcribe(
            audio, language=cfg()["transcription"]["language"], word_timestamps=True,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False, beam_size=5,
            initial_prompt=cfg()["transcription"].get("initial_prompt") or None)
        for s in segs:
            words = [{"w": w.word.strip(), "s": round(w.start + off, 2), "e": round(w.end + off, 2),
                      "p": round(w.probability, 3)} for w in (s.words or []) if w.word.strip()]
            if not words:
                continue
            result["segments"].append({
                "s": round(s.start + off, 2), "e": round(s.end + off, 2), "text": s.text.strip(),
                "logprob": round(s.avg_logprob, 3), "no_speech": round(s.no_speech_prob, 3),
                "words": words})
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    log.info("Transcript: %d segments, %d words", len(result["segments"]),
             sum(len(s["words"]) for s in result["segments"]))
    return result


def words_between(transcript: dict, start: float, end: float) -> list[dict]:
    return [w for s in transcript["segments"] if s["e"] >= start and s["s"] <= end
            for w in s["words"] if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]


def text_between(transcript: dict, start: float, end: float, stamps: bool = True) -> str:
    lines = []
    for s in transcript["segments"]:
        if s["e"] >= start and s["s"] <= end:
            lines.append(f"[{s['s'] - start:6.1f}s] {s['text']}" if stamps else s["text"])
    return "\n".join(lines)
