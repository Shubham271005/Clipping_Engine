"""Shared helpers: config, paths, logging, ffmpeg wrappers, CUDA DLL setup, Claude CLI."""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DIRS = {k: ROOT / k for k in [
    "downloads", "transcripts", "candidate_clips", "final_clips", "subtitles",
    "metadata", "analytics", "logs", "database", "secrets", "work"]}
for d in DIRS.values():
    d.mkdir(exist_ok=True)

_CFG = None
_PROFILE = None
PROFILES_DIR = ROOT / "profiles"
# config sections a content profile may override
PROFILE_OVERRIDABLE = ("transcription", "detection", "review", "editing", "metadata")


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml") if not p.stem.startswith("_"))


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown profile '{name}'. Available: {', '.join(list_profiles())}")
    base = yaml.safe_load((PROFILES_DIR / "_base.yaml").read_text(encoding="utf-8"))
    prof = yaml.safe_load(path.read_text(encoding="utf-8"))
    merged = _deep_merge(base, prof)
    if prof.get("categories"):  # a profile's category set replaces the base set entirely
        merged["categories"] = prof["categories"]
    merged["id"] = name
    return merged


def set_profile(name: str) -> dict:
    """Activate a content profile; its overrides are layered on top of settings.yaml."""
    global _PROFILE, _CFG
    _PROFILE = load_profile(name)
    _CFG = None
    return _PROFILE


def profile() -> dict:
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = load_profile(os.getenv("DEFAULT_PROFILE", "gta6"))
    return _PROFILE


def cfg() -> dict:
    global _CFG
    if _CFG is None:
        base = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        prof = profile()
        _CFG = _deep_merge(base, {k: prof[k] for k in PROFILE_OVERRIDABLE if k in prof})
    return _CFG


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S")
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        fh = logging.FileHandler(DIRS["logs"] / "engine.log", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(sh)
        log.addHandler(fh)
        log.propagate = False
    return log


log = get_logger("common")


def setup_cuda_dlls() -> None:
    """Make pip-installed NVIDIA cuBLAS/cuDNN DLLs visible to CTranslate2 on Windows."""
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------- ffmpeg helpers
def require_bin(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"'{name}' not found on PATH")
    return p


def run(cmd: list, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, **kw)
    if check and r.returncode != 0:
        err = r.stderr.decode("utf-8", "ignore") if isinstance(r.stderr, bytes) else r.stderr
        raise RuntimeError(f"Command failed ({cmd[0]}): {err[-1500:]}")
    return r


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,r_frame_rate",
             "-of", "json", path], text=True)
    j = json.loads(r.stdout)
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = any(s.get("codec_type") == "audio" for s in j.get("streams", []))
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"duration": float(j["format"]["duration"]), "width": v.get("width", 0),
            "height": v.get("height", 0), "fps": fps, "has_audio": a}


def decode_audio(path: Path, sr: int = 16000, start: float | None = None,
                 dur: float | None = None):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    raw = run(cmd).stdout
    return np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768.0


def nvenc_available() -> bool:
    try:
        r = run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                 "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
        return r.returncode == 0
    except Exception:
        return False


def video_encoder_args(quality: int = 20) -> list:
    if cfg()["editing"]["encoder"] == "h264_nvenc" and nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr",
                "-cq", str(quality), "-b:v", "0", "-maxrate", "18M", "-bufsize", "36M",
                "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(quality - 2),
            "-profile:v", "high", "-pix_fmt", "yuv420p"]


# ---------------------------------------------------------------- Claude CLI (LLM brain)
def claude_available() -> bool:
    return shutil.which("claude") is not None


def ask_claude_json(prompt: str, model: str | None = None, timeout: int = 600):
    """Ask the local Claude Code CLI for a JSON answer. Uses the user's existing plan."""
    import hashlib
    model = model or cfg()["review"]["llm_model"]
    cache = DIRS["work"] / "llm_cache"
    cache.mkdir(exist_ok=True)
    key = cache / (hashlib.sha1((model + "\n" + prompt).encode("utf-8")).hexdigest() + ".json")
    if key.exists():
        return json.loads(key.read_text(encoding="utf-8"))
    res = _ask_claude(prompt, model, timeout)
    key.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def _ask_claude(prompt: str, model: str, timeout: int):
    cmd = [shutil.which("claude"), "-p", "--output-format", "json", "--model", model,
           "--tools", "", "--no-session-persistence", "--strict-mcp-config"]
    last_err = None
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               encoding="utf-8", errors="ignore", timeout=timeout)
            outer = json.loads(r.stdout)
            if outer.get("is_error"):
                raise RuntimeError(outer.get("result", "claude error"))
            return extract_json(outer.get("result", ""))
        except Exception as e:  # retry on transient failures / malformed output
            last_err = e
            log.warning("claude call failed (attempt %d): %s", attempt + 1, str(e)[:200])
    raise RuntimeError(f"Claude CLI failed after retries: {last_err}")


def extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def fmt_ts(sec: float) -> str:
    sec = max(0, sec)
    return f"{int(sec // 3600):d}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"
