"""Animated, word-synced Shorts-style captions rendered as ASS (libass)."""
from __future__ import annotations

import re

from ..common import cfg

PROFANITY = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|cunt\w*|motherfuck\w*|asshole\w*|dick\w*|n[i1]gg\w*)\b", re.I)
EMPHASIS = re.compile(r"^(no+|yes+|what|why|how|bro+|dude|omg|wait|run|go+|dead|insane|crazy|"
                      r"wow+|holy|lets|stop|help|never|again|\d+)$", re.I)


def _mask(word: str) -> str:
    return PROFANITY.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), word)


def _ass_escape(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def group_words(words: list[dict], max_words: int) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["s"] - cur[-1]["e"] > 0.6
                    or re.search(r"[.?!,]$", cur[-1]["w"])):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], out_path, duration: float, caption_y: int,
              hook_text: str = "", hook_y: int = 230, hook_until: float | None = None,
              corrections: dict | None = None) -> None:
    e = cfg()["editing"]
    font = e["font"]
    corrections = {k.lower(): v for k, v in (corrections or {}).items()}
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},76,{e['caption_color']},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1
Style: Hook,{font},62,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook_text:
        end = hook_until if hook_until is not None else duration
        txt = _ass_escape(_mask(hook_text.upper()) if e["mask_profanity"] else hook_text.upper())
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(end)},Hook,,0,0,0,,"
                     f"{{\\pos(540,{hook_y})\\3c&H00101010&\\fad(150,150)}}{txt}")

    def clean(w: str) -> str:
        w = w.strip()
        core = re.sub(r"^[^\w']+|[^\w'?!]+$", "", w)
        core = corrections.get(core.lower().strip("?!"), core)
        if e["mask_profanity"]:
            core = _mask(core)
        return _ass_escape(core.upper())

    groups = group_words(words, e["caption_words_per_line"])
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"] + 0.25
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0]["s"])
        texts = [clean(w["w"]) for w in g]
        if not any(texts):
            continue
        for wi, w in enumerate(g):
            st = w["s"]
            en = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
            if en - st < 0.04:
                continue
            parts = []
            for k, t in enumerate(texts):
                if not t:
                    continue
                if k == wi:
                    parts.append(f"{{\\c{e['highlight_color']}\\fscx118\\fscy118\\t(0,90,\\fscx108\\fscy108)}}{t}"
                                 f"{{\\c{e['caption_color']}\\fscx100\\fscy100}}")
                elif EMPHASIS.match(t.strip("?!")):
                    parts.append(f"{{\\c{e['emphasis_color']}}}{t}{{\\c{e['caption_color']}}}")
                else:
                    parts.append(t)
            pop = "\\fad(40,0)" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\pos(540,{caption_y}){pop}}}"
                         + " ".join(parts))
    with open(out_path, "w", encoding="utf-8-sig") as f:
        f.write(header + "\n".join(lines) + "\n")
