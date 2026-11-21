"""Original commentary: context summary, 3 hooks, script, structure, graphics, payoff.

Grounding rules (RULES.md Truth):
- Claims may only come from the evidence pack: stream metadata, measured chat stats,
  sampled chat lines (public, no user ids), audio/visual detector tags and, when the
  owner allowed paid vision, an AI description of sampled frames (labelled as such).
- Never invent streamer quotes or reactions; never describe frames nobody analysed.
- GTA V footage is never called GTA 6.

Provider: Claude (Anthropic SDK, structured JSON output) when the owner enabled paid
LLM calls and the budget allows; otherwise a deterministic local writer that uses the
same evidence. Both are labelled in the project.
"""
from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import sys

from .config import ROOT, settings

STRUCTURES = {
    "MYSTERY": ["Something surprising happened", "Explain the setup", "Show the moment", "Explain why it matters"],
    "SURPRISE": ["Introduce the challenge", "Show the unexpected outcome", "Explain what happened"],
    "EDUCATIONAL": ["Introduce a game mechanic", "Show an authorized example", "Explain how the mechanic works"],
    "COMPARISON": ["Introduce two situations", "Show both examples", "Explain the differences"],
}
GAME_LABEL = {"confirmed_gameplay": "GTA 6", "gta_vi_commentary": "GTA 6", "trailer_reaction": "GTA 6 trailer",
              "gta_v_gameplay": "GTA 5", "fan_concept": "fan-made GTA concept", "unverified": "GTA"}

SCHEMA = {
    "type": "object",
    "properties": {
        "context_summary": {"type": "string"},
        "structure": {"type": "string", "enum": list(STRUCTURES)},
        "hooks": {"type": "array", "items": {"type": "string"}},
        "intro_lines": {"type": "array", "items": {"type": "string"}},
        "outro_lines": {"type": "array", "items": {"type": "string"}},
        "graphics": {"type": "array", "items": {"type": "string"}},
        "payoff": {"type": "string"},
        "cover_text": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "claim": {"type": "string"}, "evidence": {"type": "string"}}, "required": ["claim", "evidence"],
            "additionalProperties": False}},
    },
    "required": ["context_summary", "structure", "hooks", "intro_lines", "outro_lines", "graphics", "payoff",
                 "cover_text", "claims"],
    "additionalProperties": False,
}

SYSTEM = """You write original English voiceover for 25-45 second YouTube Shorts on the channel GTA VI Central.
Voice: a calm, confident narrator explaining a moment to a friend; short spoken sentences; no hype words like
"insane" in the narration; no emojis. Hard rules:
1. Use ONLY facts in the evidence JSON. Every factual sentence must map to an entry in `claims` with its evidence.
2. Never quote or paraphrase the streamer. Never describe what is on screen unless the evidence contains a
   frame description, and then say only what it says.
3. Use the game label exactly as given in evidence.game_label (GTA 5 footage is never GTA 6).
4. intro_lines play before the moment (max 28 words total, must end before the moment starts);
   outro_lines play after it (max 26 words total) and deliver the payoff and one specific question for comments.
5. hooks: exactly 3 alternative first lines, each under 14 words, naming the game and a concrete detail.
6. cover_text: at most 3 words, no repeated words.
Add value: explain WHY the audience reacted, using the measured chat numbers."""


def evidence_pack(cand, ls, chat_lines: list[str], media_tags: dict, frame_desc: str | None) -> dict:
    exp = cand.explanation or {}
    pm = exp.get("peak_metrics", {})
    no_chat = bool(exp.get("no_chat")) or ls.platform == "upload"
    return {
        "game_label": GAME_LABEL.get(ls.content_type, "GTA"),
        "content_type": ls.content_type,
        "stream": {"title": ls.title, "channel": ls.channel_name, "platform": ls.platform,
                   "simulation": bool(ls.is_simulation)},
        "moment": {"media_s": cand.media_ts, "how_found": exp.get("timing", {}).get("method")},
        "chat": None if no_chat else {"messages_per_second_at_peak": pm.get("rate_10s"), "baseline_messages_per_second": pm.get("baseline_rate"),
                 "multiple_of_baseline": round((pm.get("rate_10s") or 0) / max(pm.get("baseline_rate") or 0.1, 0.1), 1),
                 "users_reacting_in_10s": pm.get("unique_users_10s"), "top_reactions": pm.get("top_reactions"),
                 "dominant_reaction": cand.dominant_reaction, "spikes_merged": cand.spike_count,
                 "sample_lines": chat_lines[:25]},
        "media_signals": media_tags,
        "frame_description": frame_desc,
        "rights": cand.rights_state,
    }


def pick_structure(reaction: str | None, content_type: str) -> str:
    if content_type == "gta_vi_commentary":
        return "EDUCATIONAL"
    return {"HOW": "EDUCATIONAL", "WHAT JUST HAPPENED": "MYSTERY", "DID YOU SEE": "MYSTERY",
            "LOL": "SURPRISE", "LMAO": "SURPRISE", "REPLAY": "SURPRISE", "CLIP": "SURPRISE"}.get(reaction or "", "SURPRISE")


def local_writer(ev: dict) -> dict:
    """Deterministic, evidence-only writer (no paid calls)."""
    g = ev["game_label"]
    c = ev["chat"]
    if c is None:
        return _local_writer_no_chat(ev)
    mult = c["multiple_of_baseline"] or 1
    rate = c["messages_per_second_at_peak"] or 0
    base = c["baseline_messages_per_second"] or 0
    users = c["users_reacting_in_10s"] or 0
    react = (c["dominant_reaction"] or "reaction").lower()
    top = [k.lower() for k in (c["top_reactions"] or {})][:3]
    structure = pick_structure(c["dominant_reaction"], ev["content_type"])
    ch = ev["stream"]["channel"]
    hooks = [
        f"This {g} moment made chat {mult:.0f} times faster in ten seconds.",
        f"{users} people typed '{react}' at the same second during this {g} stream.",
        f"Chat was calm at {base:.0f} messages a second. Then this {g} moment hit.",
    ]
    intro = [hooks[0], f"Normal chat here ran about {base:.0f} messages a second.", "Watch the next few seconds."]
    outro = [f"Right there chat jumped to {rate:.0f} a second, {users} different people in ten seconds.",
             f"The top reactions were {', '.join(top) or react}.",
             "Would you have reacted the same way? Tell me below."]
    tags = ev.get("media_signals") or {}
    claims = [
        {"claim": f"chat ran ~{base:.1f} msg/s before the moment", "evidence": "measured baseline (rolling median, 5 min)"},
        {"claim": f"chat peaked at ~{rate:.1f} msg/s (x{mult:.1f})", "evidence": "measured 10 s window at peak"},
        {"claim": f"{users} distinct users reacted in 10 s", "evidence": "salted-hash unique count"},
        {"claim": f"top reactions: {', '.join(top)}", "evidence": "keyword/emoji lexicon counts"},
        {"claim": f"footage labelled {g}", "evidence": f"stream classification: {ev['content_type']}"},
    ]
    summary = (f"{ch} ({ev['stream']['platform']}) - '{ev['stream']['title']}'. A chat spike x{mult:.1f} over baseline, "
               f"led by '{react}'. Media signals: {', '.join(sum(tags.values(), [])) if tags else 'none (no media analysed)'}.")
    words = {"CLIP": "CHAT SAID CLIP", "NO WAY": "NO WAY", "HOW": "HOW?", "WHAT JUST HAPPENED": "WAIT, WHAT",
             "OMG": "CHAT EXPLODED", "INSANE": "CHAT EXPLODED", "REPLAY": "RUN IT BACK"}
    return {"context_summary": summary, "structure": structure, "hooks": hooks, "intro_lines": intro,
            "outro_lines": outro, "graphics": [f"chat-speed meter {base:.0f} -> {rate:.0f}/s", "reaction word cloud",
                                               "moment marker on the timeline"],
            "payoff": outro[0], "cover_text": words.get(c["dominant_reaction"] or "", "CHAT EXPLODED"), "claims": claims}


def _local_writer_no_chat(ev: dict) -> dict:
    """Owner upload without chat: talk only about what the detectors measured."""
    g = ev["game_label"]
    tags = [t for t in sum((ev.get("media_signals") or {}).values(), []) if t]
    m = ev.get("moment") or {}
    sig = tags[0] if tags else "the clearest change in the clip"
    hooks = [f"Watch the exact second this {g} clip changes.",
             f"This {g} moment has the biggest jump in the whole recording.",
             f"Most people skip past this {g} moment. Don't."]
    intro = [hooks[0], "Keep your eyes on the middle of the screen."]
    outro = [f"That was the peak: {sig}.", "Did you catch it the first time? Tell me below."]
    claims = [{"claim": f"moment at {m.get('media_s') or 0:.1f}s of the upload", "evidence": m.get("how_found") or "owner"},
              {"claim": f"signal: {sig}", "evidence": "audio/visual detector tags"},
              {"claim": f"footage labelled {g}", "evidence": f"owner classification: {ev['content_type']}"}]
    return {"context_summary": f"Owner upload '{ev['stream']['title']}'. Signals: {', '.join(tags) or 'none'}.",
            "structure": "MYSTERY", "hooks": hooks, "intro_lines": intro, "outro_lines": outro,
            "graphics": ["audio level meter", "moment marker"], "payoff": outro[0], "cover_text": "WATCH THIS", "claims": claims}


def claude_writer(ev: dict, frames: list[pathlib.Path]) -> tuple[dict, dict]:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_key or None)
    content: list[dict] = []
    for f in frames[:3]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(f.read_bytes()).decode()}})
    note = ("The images are frames sampled from the authorized clip around the moment; you may describe only what "
            "is clearly visible in them, and record such claims with evidence 'sampled frame'.") if frames else ""
    content.append({"type": "text", "text": f"{note}\nEvidence JSON:\n{json.dumps(ev, ensure_ascii=False)}"})
    try:
        r = client.messages.create(
            model=settings.llm_model, max_tokens=4000, system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}])
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"Anthropic rate limited: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Anthropic: network error") from e
    if r.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    text = next(b.text for b in r.content if b.type == "text")
    return json.loads(text), {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens, "model": r.model}


# ---------------------------------------------------------------- quality gates from the channel skills
def _skill(name: str) -> pathlib.Path | None:
    p = pathlib.Path.home() / ".claude" / "skills" / name
    return p if p.exists() else None


def hookscore(hooks: list[str]) -> list[dict]:
    script = _skill("yt-script/hookscore.py")
    if not script:
        return [{"hook": h, "score": None, "note": "hookscore.py not installed"} for h in hooks]
    tmp = settings.data_dir / "_hooks.txt"
    tmp.write_text("\n".join(hooks), encoding="utf-8")
    p = subprocess.run([sys.executable, str(script), "--json", str(tmp)], capture_output=True, text=True, encoding="utf-8")
    try:
        rows = json.loads(p.stdout)
        return [{"hook": r.get("hook") or r.get("text"), "score": r.get("verdict", r.get("score")), "band": r.get("band"),
                 "formula": r.get("formula")} for r in rows]
    except Exception:
        return [{"hook": h, "score": None, "note": p.stderr[-200:]} for h in hooks]


def title_lint(titles: list[str], thumb: str | None = None) -> list[dict]:
    script = _skill("yt-package/title.py")
    out = []
    for t in titles:
        if not script:
            out.append({"title": t, "score": None, "issues": ["title.py not installed"]})
            continue
        args = [sys.executable, str(script), "--title", t, "--json"] + (["--thumb", thumb] if thumb else [])
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        try:
            r = json.loads(p.stdout)
            r = r[0] if isinstance(r, list) else r
            out.append({"title": t, "score": r.get("score"), "issues": [i[1] if isinstance(i, list) else i for i in r.get("issues", [])]})
        except Exception:
            out.append({"title": t, "score": None, "issues": [p.stderr[-200:]]})
    return out
