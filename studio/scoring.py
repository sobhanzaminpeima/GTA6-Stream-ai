"""Explainable viral-candidate score (0-100) and chat-to-media timestamp mapping.

Score = weighted mean of the available components. When audio/visual evidence is
missing (no media rights, or media not analysed yet) the weights are renormalised
over what exists and the result is labelled REDUCED confidence. Tiers are editorial
heuristics, not view predictions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_WEIGHTS = {"chat": 0.30, "audio": 0.20, "visual": 0.20, "narrative": 0.20, "originality": 0.10}

# reactions that usually mean "a self-contained moment with a payoff happened"
NARRATIVE_REACTIONS = {"CLIP": 18, "REPLAY": 20, "HOW": 22, "WHAT JUST HAPPENED": 22, "NO WAY": 16,
                       "DID YOU SEE": 18, "INSANE": 14, "WILD": 12, "WTF": 12, "OMG": 10, "CRAZY": 10,
                       "LMAO": 8, "LOL": 6, "HYPE": 4}


def tier(score: float) -> str:
    if score >= 90:
        return "Exceptional candidate"
    if score >= 75:
        return "High priority"
    if score >= 60:
        return "Review recommended"
    return "Low priority"


def chat_component(peak_combined: float, spike_count: int, unique_users: int) -> float:
    base = 100 * (1 - math.exp(-max(peak_combined, 0) / 7.0))
    sustain = min(10.0, 5.0 * (spike_count - 1))
    breadth = min(8.0, max(0, unique_users - 10) * 0.25)
    return round(min(100.0, base + sustain + breadth), 1)


def narrative_component(reactions: dict[str, float], duration_s: float, has_buildup: bool) -> tuple[float, str]:
    total = sum(reactions.values()) or 1.0
    mix = sum(NARRATIVE_REACTIONS.get(r, 3) * w / total for r, w in reactions.items())
    score = 35 + mix * 1.6 + (10 if has_buildup else 0) + (8 if 8 <= duration_s <= 90 else 0)
    top = sorted(reactions, key=reactions.get, reverse=True)[:2]
    why = f"reaction mix {', '.join(top) or 'none'}" + ("; chat built up before the peak" if has_buildup else "")
    return round(min(score, 100.0), 1), why


def originality_component(similar_recent: int, owned_footage: bool) -> tuple[float, str]:
    s = 70 - 15 * similar_recent + (20 if owned_footage else 0)
    why = f"{similar_recent} similar moments produced in the last 7 days" + ("; footage is ours" if owned_footage else "")
    return round(max(0.0, min(100.0, s)), 1), why


@dataclass
class ScoreResult:
    overall: float
    confidence: str
    tier: str
    components: dict
    weights_used: dict
    explanation: list[str]


def combine(components: dict[str, float | None], weights: dict[str, float] | None = None,
            notes: dict[str, str] | None = None) -> ScoreResult:
    weights = weights or DEFAULT_WEIGHTS
    avail = {k: v for k, v in components.items() if v is not None and weights.get(k, 0) > 0}
    wsum = sum(weights[k] for k in avail) or 1.0
    used = {k: round(weights[k] / wsum, 3) for k in avail}
    overall = round(sum(avail[k] * used[k] for k in avail), 1)
    missing = [k for k in weights if components.get(k) is None]
    if not missing:
        conf = "high"
    elif "audio" in missing and "visual" in missing:
        conf = "reduced (chat-only)"
    else:
        conf = "medium"
    expl = [f"{k}: {avail[k]:.0f} x {used[k]:.2f}" + (f" ({notes[k]})" if notes and notes.get(k) else "") for k in avail]
    if missing:
        expl.append("missing: " + ", ".join(missing) + " -> weights renormalised over available evidence")
    return ScoreResult(overall, conf, tier(overall), {k: components.get(k) for k in weights}, used, expl)


# ---------------------------------------------------------------- timing
@dataclass
class MediaTime:
    media_ts: float | None
    uncertainty_s: float | None
    method: str


def estimate_event_wall(onset_wall: float, chat_latency_s: float) -> float:
    """Chat reacts after the viewer saw it: event happened ~latency before the onset."""
    return onset_wall - chat_latency_s


def map_to_media(event_wall: float, mappings: list[tuple[float, float, float]], latency_uncertainty: float = 3.0) -> MediaTime:
    """mappings: (wall_ts, media_ts, uncertainty) anchors, e.g. one per ingested segment.
    Uses the latest anchor at or before the event (or the nearest one)."""
    if not mappings:
        return MediaTime(None, None, "no media timeline (source not recorded)")
    before = [m for m in mappings if m[0] <= event_wall]
    anchor = max(before, key=lambda m: m[0]) if before else min(mappings, key=lambda m: abs(m[0] - event_wall))
    media = anchor[1] + (event_wall - anchor[0])
    unc = math.sqrt(anchor[2] ** 2 + latency_uncertainty ** 2 + 0.5 ** 2)
    return MediaTime(round(media, 2), round(unc, 2), "segment anchor + chat latency offset")


def calibrated_latency(samples: list[float], default: float) -> float:
    """samples = (onset_wall - true_event_wall) the owner confirmed on past candidates."""
    if len(samples) < 3:
        return default
    s = sorted(samples)
    return float(s[len(s) // 2])
