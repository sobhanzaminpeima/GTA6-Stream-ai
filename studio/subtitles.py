"""Caption cues from word timings -> SRT + ASS (burned in by the renderer).

Style: Bahnschrift, white text, yellow emphasis, dark outline, on a dark plate (channel
v2 look). Safe area for Shorts: nothing in the bottom 20% (Shorts UI) or the right
~12% (button column); captions sit centred ~62% down the frame.
"""
from __future__ import annotations

import pathlib
import re

W, H = 1080, 1920
SAFE_BOTTOM = int(H * 0.20)
SAFE_RIGHT = int(W * 0.12)
EMPH = re.compile(r"^(\d[\d,.]*%?|x\d+|gta|6|vi|clip|no|way|omg|chat|insane|wild|replay|wait|what|why|how)$", re.I)


def chunk(words: list[dict], max_words: int = 3, max_chars: int = 20, max_gap: float = 0.35) -> list[dict]:
    """Group words into short cues. Never splits 'GTA' from its number (gauntlet lesson)."""
    cues, cur = [], []
    for i, w in enumerate(words):
        txt = w["w"]
        if cur:
            joined = " ".join(x["w"] for x in cur + [w])
            prev = cur[-1]["w"]
            keep_pair = prev.strip(",.").upper() == "GTA"
            too_long = len(cur) >= max_words or len(joined) > max_chars
            gap = w["s"] - cur[-1]["e"] > max_gap
            punct = re.search(r"[.?!,;:]$", prev)
            if not keep_pair and (too_long or gap or punct):
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    out = []
    for c in cues:
        out.append({"start": c[0]["s"], "end": max(c[-1]["e"], c[0]["s"] + 0.35), "words": [x["w"] for x in c]})
    for a, b in zip(out, out[1:]):  # no overlaps, no flicker gaps < 0.12 s
        if b["start"] - a["end"] < 0.12:
            a["end"] = max(a["start"] + 0.2, b["start"] - 0.02)
    return out


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def to_srt(cues: list[dict]) -> str:
    return "".join(f"{i}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n{' '.join(c['words'])}\n\n" for i, c in enumerate(cues, 1))


def to_ass(cues: list[dict], style: dict | None = None) -> str:
    st = {"font": "Bahnschrift", "size": 86, "primary": "&H00FFFFFF", "emphasis": "&H004DE1FF",  # BGR: #FFE14D
          "outline": 6, "plate": True, "animate": True, "margin_v": 440, **(style or {})}
    border = 3 if st["plate"] else 1
    back = "&H9A0A0710" if st["plate"] else "&H80000000"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{st['primary']},&H000000FF,&H00100A09,{back},1,0,0,0,100,100,1,0,{border},{st['outline']},0,2,90,{SAFE_RIGHT + 30},{st['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        words = []
        for w in c["words"]:
            clean = w.strip(",.!?;:\"'").upper()
            disp = w.upper().replace("{", "(").replace("}", ")")
            words.append(f"{{\\c{st['emphasis']}}}{disp}{{\\c{st['primary']}}}" if EMPH.match(clean) else disp)
        anim = "{\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}" if st["animate"] else ""
        lines.append(f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Cap,,0,0,0,,{anim}{' '.join(words)}")
    return head + "\n".join(lines) + "\n"


def write(cues: list[dict], folder: pathlib.Path, style: dict | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    srt, ass = folder / "captions.srt", folder / "captions.ass"
    srt.write_text(to_srt(cues), encoding="utf-8")
    ass.write_text(to_ass(cues, style), encoding="utf-8-sig")
    return srt, ass


def parse_srt(text: str) -> list[dict]:
    """For manual corrections made in the editor."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = [x.strip() for x in lines[1].split("-->")]

            def sec(x):
                h, m, rest = x.split(":")
                s, ms = rest.split(",")
                return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            cues.append({"start": sec(a), "end": sec(b), "words": " ".join(lines[2:]).split()})
    return cues


def safe_zone_ok(style: dict | None = None) -> dict:
    mv = (style or {}).get("margin_v", 440)
    size = (style or {}).get("size", 86)
    return {"bottom_margin_px": mv, "required_px": SAFE_BOTTOM, "ok": mv >= SAFE_BOTTOM + 20,
            "right_margin_px": SAFE_RIGHT + 30, "line_height_px": size}
