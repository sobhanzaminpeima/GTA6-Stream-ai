"""Turn raw platform chat messages into normalized, privacy-minimized events.

Lightweight and local: no message ever goes to an LLM. Reaction detection is a
weighted lexicon (EN + common multilingual reactions + emoji), with fuzzy handling of
stretched words ("NOOOO WAYYY") and repeated phrases.
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field

# phrase -> (canonical reaction, weight). Multi-word phrases are matched on the
# collapsed text; weights are editorial defaults, overridable in Automation settings.
REACTIONS: dict[str, tuple[str, float]] = {
    "clip that": ("CLIP", 3.0), "clip it": ("CLIP", 3.0), "clip": ("CLIP", 2.5), "clipped": ("CLIP", 2.0),
    "omg": ("OMG", 1.5), "oh my god": ("OMG", 1.5), "oh my gosh": ("OMG", 1.3),
    "no way": ("NO WAY", 2.0), "nah no way": ("NO WAY", 2.0),
    "wtf": ("WTF", 1.8), "what the": ("WTF", 1.2),
    "insane": ("INSANE", 2.0), "insanity": ("INSANE", 1.8),
    "what just happened": ("WHAT JUST HAPPENED", 2.5), "what happened": ("WHAT JUST HAPPENED", 1.5),
    "lol": ("LOL", 1.0), "lmao": ("LMAO", 1.2), "lmfao": ("LMAO", 1.3), "rofl": ("LOL", 1.0),
    "crazy": ("CRAZY", 1.6), "replay": ("REPLAY", 2.5), "run it back": ("REPLAY", 2.5),
    "how did he do that": ("HOW", 2.5), "how did she do that": ("HOW", 2.5), "how did they do that": ("HOW", 2.5),
    "how": ("HOW", 0.6), "this is wild": ("WILD", 2.0), "wild": ("WILD", 1.2),
    "did you see that": ("DID YOU SEE", 2.2), "yo": ("HYPE", 0.5), "lets go": ("HYPE", 1.4),
    "pog": ("HYPE", 1.4), "poggers": ("HYPE", 1.4), "pogchamp": ("HYPE", 1.4), "kekw": ("LOL", 1.0),
    "bruh": ("WTF", 0.8), "holy": ("OMG", 1.2), "unreal": ("INSANE", 1.6),
    # multilingual
    "no puede ser": ("NO WAY", 2.0), "que locura": ("INSANE", 1.8), "jajaja": ("LOL", 1.0),
    "kkkk": ("LOL", 1.0), "mds": ("OMG", 1.3), "nossa": ("OMG", 1.3), "krass": ("INSANE", 1.6),
    "alter": ("WTF", 0.8), "putain": ("WTF", 1.4), "mdr": ("LOL", 1.0), "dios mio": ("OMG", 1.5),
    "ахах": ("LOL", 1.0), "жесть": ("INSANE", 1.6), "やばい": ("INSANE", 1.6), "草": ("LOL", 1.0),
}
REACTION_EMOJI: dict[str, tuple[str, float]] = {
    "😱": ("OMG", 1.2), "🤯": ("INSANE", 1.4), "😂": ("LOL", 0.8), "🤣": ("LOL", 0.8), "💀": ("LOL", 0.9),
    "🔥": ("HYPE", 0.8), "😮": ("OMG", 1.0), "😳": ("OMG", 1.0), "👀": ("DID YOU SEE", 0.6), "🚨": ("HYPE", 0.7),
}
POSITIVE = {"lets go", "pog", "insane", "nice", "goat", "fire", "amazing", "clean", "w", "hype", "love"}
NEGATIVE = {"boring", "l", "trash", "cringe", "fake", "sad", "rip", "bad", "worst"}
SPAM_PATTERNS = [
    (re.compile(r"https?://|www\.|\.com\b|\.gg/", re.I), "link"),
    (re.compile(r"\b(free|giveaway|subscribe to my|follow me|check my channel|sub4sub)\b", re.I), "promo"),
    (re.compile(r"(.)\1{14,}"), "char_flood"),
]
GIVEAWAY = re.compile(r"\bgiveaway\b|(?:^|\s)!(enter|join|raffle|ticket)\b", re.I)
MOD_NOTICE = re.compile(r"^(\[mod\]|mod:|moderator:|!rules|slow mode|subscribers-only)", re.I)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
_STRETCH = re.compile(r"([a-z])\1{2,}")


def _salt() -> bytes:
    from ..config import settings
    return settings.secret_key[:16]


def hash_user(platform: str, user_id: str) -> str:
    """Salted, truncated hash: lets us count unique reacting users without storing who they are."""
    return hashlib.blake2b(f"{platform}:{user_id}".encode(), key=_salt(), digest_size=12).hexdigest()


def normalize_text(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    t = EMOJI_RE.sub(" ", t)
    t = re.sub(r"[^\w\s']", " ", t)
    t = _STRETCH.sub(r"\1\1", t)  # "noooo" -> "noo"
    t = re.sub(r"\b(no+)\b", "no", t)
    t = re.sub(r"\b(wa+y+)\b", "way", t)
    t = re.sub(r"\b(o+m+g+)\b", "omg", t)
    t = re.sub(r"\b(lo+l+)\b", "lol", t)
    t = re.sub(r"\b(cli+p+)\b", "clip", t)
    t = re.sub(r"\b(ye+s+)\b", "yes", t)
    t = re.sub(r"\b(let'?s+ go+)\b", "lets go", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Normalized:
    normalized: str
    emoji_count: int
    keywords: list[str]
    reaction_weight: float
    sentiment: float
    is_spam: bool
    spam_reason: str | None
    kind: str
    dominant: str | None = None
    reactions: dict[str, float] = field(default_factory=dict)


class Normalizer:
    """Stateful per stream: tracks repeat/copy-paste spam and per-user flooding."""

    def __init__(self, custom_keywords: dict[str, float] | None = None, weight_overrides: dict[str, float] | None = None):
        self.lex = dict(REACTIONS)
        for k, w in (custom_keywords or {}).items():
            self.lex[normalize_text(k)] = (k.upper(), float(w))
        self.overrides = {k.upper(): float(v) for k, v in (weight_overrides or {}).items()}
        self._recent_texts: deque[tuple[float, str, str]] = deque(maxlen=400)
        self._user_times: dict[str, deque] = {}
        self._phrases = sorted(self.lex, key=len, reverse=True)

    def process(self, text: str, user_hash: str, ts: float | None = None, is_bot: bool = False,
                kind_hint: str | None = None) -> Normalized:
        ts = ts if ts is not None else time.time()
        norm = normalize_text(text)
        emojis = EMOJI_RE.findall(text)
        reactions: dict[str, float] = {}
        padded = f" {norm} "
        consumed = padded
        for ph in self._phrases:
            pat = f" {ph} "
            if pat in consumed:
                canon, w = self.lex[ph]
                n = consumed.count(pat)
                reactions[canon] = reactions.get(canon, 0.0) + w * min(n, 3) ** 0.5
                consumed = consumed.replace(pat, " ")
        for e in emojis:
            if e in REACTION_EMOJI:
                canon, w = REACTION_EMOJI[e]
                reactions[canon] = reactions.get(canon, 0.0) + w
        for canon in list(reactions):
            if canon in self.overrides:
                reactions[canon] *= self.overrides[canon]
        # caps/exclamation intensity: shouting in chat is a reaction signal too
        letters = [c for c in text if c.isalpha()]
        caps = sum(c.isupper() for c in letters) / len(letters) if len(letters) >= 4 else 0.0
        intensity = 1.0 + 0.3 * (caps > 0.7) + 0.1 * min(text.count("!"), 3)
        weight = min(sum(reactions.values()) * intensity, 8.0)

        words = set(norm.split())
        pos = len(words & POSITIVE) + (1 if "lets go" in norm else 0)
        neg = len(words & NEGATIVE)
        sentiment = (pos - neg) / max(pos + neg, 1)

        # ---- spam / special events
        kind = kind_hint or "message"
        spam, reason = False, None
        if is_bot:
            spam, reason = True, "bot_account"
        if MOD_NOTICE.search(text):
            kind = "mod"
        if GIVEAWAY.search(text):
            kind = "giveaway"
        for pat, why in SPAM_PATTERNS:
            if not spam and pat.search(text):
                spam, reason = True, why
        q = self._user_times.setdefault(user_hash, deque(maxlen=20))
        q.append(ts)
        if not spam and len(q) >= 6 and ts - q[-6] < 5.0:
            spam, reason = True, "user_flood"
        # identical long message from many users within 20 s = copypasta (often creator-requested spam)
        if not spam and len(norm) > 25:
            same = sum(1 for t0, tx, u in self._recent_texts if tx == norm and ts - t0 < 20 and u != user_hash)
            if same >= 3:
                spam, reason = True, "copypasta"
        self._recent_texts.append((ts, norm, user_hash))
        dominant = max(reactions, key=reactions.get) if reactions else None
        return Normalized(norm, len(emojis), sorted(reactions), 0.0 if spam else weight, sentiment,
                          spam, reason, kind, dominant, reactions)
