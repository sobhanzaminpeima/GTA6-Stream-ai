"""Statistical chat-spike detection (local, cheap, explainable).

Per stream, per second, we aggregate messages / unique users / reaction weight /
emoji / spam. Every second we compare the 10 s window to a stream-specific baseline
built from the trailing 5 minutes (excluding the last 30 s so a building spike does
not raise its own baseline):

  robust z = (x - median) / (1.4826 * MAD)          (resists earlier spikes)
  EWMA z   = (x - ewma) / sqrt(ewvar)                (tracks slow drift)
  velocity = rate_5s / rate_60s                      (sudden acceleration)
  CUSUM    = one-sided cumulative sum on robust z    (sustained change-point)

A spike fires when the combined score passes the sensitivity threshold, enough
messages and distinct users are involved, and audience-reaction content is present.
Known false-positive causes are suppressed and logged with a reason: raids,
giveaways, moderator announcements, reconnect backlogs, copy-paste / creator-requested
spam and single-user floods. Nearby spikes merge into one moment.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

WINDOWS = (5, 10, 30, 60, 300)


@dataclass
class Bucket:
    second: int
    messages: int = 0
    users: set = field(default_factory=set)
    reaction: float = 0.0
    emojis: int = 0
    spam: int = 0
    sentiment: float = 0.0
    kinds: Counter = field(default_factory=Counter)
    reactions: Counter = field(default_factory=Counter)
    texts: Counter = field(default_factory=Counter)


@dataclass
class Spike:
    second: int
    score: float
    metrics: dict
    dominant: str | None
    suppressed: bool
    suppress_reason: str | None
    explanation: str


@dataclass
class Moment:
    """A merged group of spikes = one candidate."""
    first_second: int
    last_second: int
    onset_second: int
    peak_score: float
    peak_metrics: dict
    reactions: Counter
    spikes: list[Spike]
    closed: bool = False

    @property
    def dominant(self) -> str | None:
        return self.reactions.most_common(1)[0][0] if self.reactions else None


class SpikeDetector:
    def __init__(self, sensitivity: float = 3.5, min_messages: int = 8, min_users: int = 4,
                 merge_gap_s: int = 45, warmup_s: int = 60, max_per_hour: int = 6):
        self.sensitivity = sensitivity
        self.min_messages = min_messages
        self.min_users = min_users
        self.merge_gap_s = merge_gap_s
        self.warmup_s = warmup_s
        self.max_per_hour = max_per_hour
        self.buckets: dict[int, Bucket] = {}
        self.history: deque[tuple[int, float, float, float]] = deque(maxlen=600)  # (sec, rate10, react10, users10)
        self.first_second: int | None = None
        self.last_closed_second: int | None = None
        self.ewma = {"rate": None, "react": None}
        self.ewvar = {"rate": 1.0, "react": 1.0}
        self.cusum = 0.0
        self.last_raid: int | None = None
        self.last_message_second: int | None = None
        self.gap_before: dict[int, int] = {}
        self.open: Moment | None = None
        self.moments: list[Moment] = []
        self.timeline: list[dict] = []
        self.spikes: list[Spike] = []
        self.emitted_times: deque[int] = deque()

    # ---------------------------------------------------------------- ingest
    def add(self, ts: float, user_hash: str, reaction_weight: float, emoji_count: int, is_spam: bool,
            kind: str = "message", reactions: dict | None = None, sentiment: float = 0.0, normalized: str = "") -> None:
        sec = int(ts)
        if self.first_second is None:
            self.first_second = sec
        b = self.buckets.get(sec)
        if b is None:
            b = self.buckets[sec] = Bucket(sec)
        if kind == "raid":
            self.last_raid = sec
        b.kinds[kind] += 1
        if kind in ("raid", "system"):
            return
        if self.last_message_second is not None and sec - self.last_message_second > 20:
            self.gap_before[sec] = sec - self.last_message_second
        self.last_message_second = max(sec, self.last_message_second or sec)
        b.messages += 1
        b.users.add(user_hash)
        b.emojis += emoji_count
        if is_spam:
            b.spam += 1
        else:
            b.reaction += reaction_weight
            b.sentiment += sentiment
            for r, w in (reactions or {}).items():
                b.reactions[r] += w
        if normalized and len(normalized) > 3:
            b.texts[normalized] += 1

    # ---------------------------------------------------------------- windows
    def _window(self, end: int, size: int) -> dict:
        msgs = users_n = spam = emojis = 0
        react = sent = 0.0
        users: set = set()
        kinds: Counter = Counter()
        reactions: Counter = Counter()
        texts: Counter = Counter()
        for s in range(end - size + 1, end + 1):
            b = self.buckets.get(s)
            if not b:
                continue
            msgs += b.messages
            users |= b.users
            react += b.reaction
            spam += b.spam
            emojis += b.emojis
            sent += b.sentiment
            kinds.update(b.kinds)
            reactions.update(b.reactions)
            texts.update(b.texts)
        users_n = len(users)
        return {"messages": msgs, "rate": msgs / size, "unique_users": users_n, "reaction": react,
                "spam": spam, "emojis": emojis, "sentiment": sent / max(msgs - spam, 1),
                "kinds": kinds, "reactions": reactions, "texts": texts}

    def _robust(self, xs: list[float], x: float, floor: float = 0.3) -> tuple[float, float, float]:
        med = statistics.median(xs)
        mad = statistics.median([abs(v - med) for v in xs])
        scale = max(1.4826 * mad, 0.25 * med, floor)
        return (x - med) / scale, med, mad

    # ---------------------------------------------------------------- tick
    def tick(self, now_second: int) -> list[Spike]:
        """Evaluate every second up to now_second. Returns spikes that fired."""
        fired: list[Spike] = []
        if self.first_second is None:
            return fired
        start = (self.timeline[-1]["second"] + 1) if self.timeline else self.first_second
        for sec in range(start, now_second + 1):
            sp = self._evaluate(sec)
            if sp:
                fired.append(sp)
            # keep memory bounded: drop buckets older than the longest window
            old = sec - max(WINDOWS) - 5
            for k in [k for k in self.buckets if k < old]:
                del self.buckets[k]
        return fired

    def _evaluate(self, sec: int) -> Spike | None:
        w5, w10, w60 = self._window(sec, 5), self._window(sec, 10), self._window(sec, 60)
        w30 = self._window(sec, 30)
        hist = [h for h in self.history if h[0] <= sec - 30]
        rate, react, users = w10["rate"], w10["reaction"], w10["unique_users"]
        z_rate = z_react = z_users = 0.0
        med_rate = mad_rate = 0.0
        ready = self.first_second is not None and sec - self.first_second >= self.warmup_s and len(hist) >= 20
        if ready:
            z_rate, med_rate, mad_rate = self._robust([h[1] for h in hist], rate)
            # reaction weight is ~0 in calm chat: floor the scale at ~2 strong reactions so a
            # couple of "CLIP"s can't explode the z-score on their own
            z_react, _, _ = self._robust([h[2] for h in hist], react, floor=5.0)
            z_users, _, _ = self._robust([h[3] for h in hist], users, floor=2.0)
        # EWMA baseline (alpha ~ 1/60 s)
        ez = {}
        for k, x in (("rate", rate), ("react", react)):
            m = self.ewma[k]
            if m is None:
                self.ewma[k], ez[k] = x, 0.0
            else:
                ez[k] = (x - m) / math.sqrt(max(self.ewvar[k], 0.09))
                if not (ready and (z_rate > self.sensitivity)):  # don't learn from spikes
                    a = 1 / 60
                    d = x - m
                    self.ewma[k] = m + a * d
                    self.ewvar[k] = (1 - a) * (self.ewvar[k] + a * d * d)
        velocity = w5["rate"] / max(w60["rate"], 0.2)
        self.cusum = max(0.0, self.cusum + min(z_rate, 6) - 1.0)
        combined = 0.0
        if ready:
            combined = (0.35 * min(z_rate, 12) + 0.35 * min(z_react, 12) + 0.15 * min(z_users, 12)
                        + 0.10 * min(ez.get("rate", 0), 12) + 0.05 * min(math.log2(max(velocity, 1)) * 2, 6))
        self.history.append((sec, rate, react, float(users)))
        b = self.buckets.get(sec)
        self.timeline.append({"second": sec, "messages": b.messages if b else 0,
                              "unique_users": len(b.users) if b else 0, "reaction": round(b.reaction, 2) if b else 0.0,
                              "emojis": b.emojis if b else 0, "spam": b.spam if b else 0,
                              "score": round(combined, 3), "rate10": round(rate, 2)})
        del self.timeline[:-3600]

        result = None
        # a spike needs a real volume rise, not only reaction words in normal-speed chat
        volume_ok = z_rate >= 2.0 and rate >= 1.6 * max(med_rate, 0.5)
        if ready and volume_ok and combined >= self.sensitivity and w10["messages"] >= self.min_messages:
            metrics = {
                "rate_5s": round(w5["rate"], 2), "rate_10s": round(rate, 2), "rate_30s": round(w30["rate"], 2),
                "rate_60s": round(w60["rate"], 2), "baseline_rate": round(med_rate, 2), "baseline_mad": round(mad_rate, 2),
                "z_rate": round(z_rate, 2), "z_reaction": round(z_react, 2), "z_users": round(z_users, 2),
                "ewma_z": round(ez.get("rate", 0), 2), "velocity": round(velocity, 2), "cusum": round(self.cusum, 2),
                "unique_users_10s": users, "reaction_10s": round(react, 2), "emojis_10s": w10["emojis"],
                "spam_share": round(w10["spam"] / max(w10["messages"], 1), 2),
                "sentiment": round(w10["sentiment"], 2), "combined": round(combined, 2),
                "top_reactions": dict(w10["reactions"].most_common(4)),
            }
            reason = self._suppression(sec, w10, w30)
            dominant = w10["reactions"].most_common(1)[0][0] if w10["reactions"] else None
            expl = (f"{w10['messages']} messages in 10 s from {users} users ({rate:.1f}/s vs baseline "
                    f"{med_rate:.1f}/s, robust z={z_rate:.1f}); reaction weight z={z_react:.1f}"
                    + (f", led by '{dominant}'" if dominant else "") + f"; velocity x{velocity:.1f}.")
            if reason:
                expl += f" SUPPRESSED: {reason}."
            result = Spike(sec, combined, metrics, dominant, bool(reason), reason, expl)
            self.spikes.append(result)
            if not reason:
                self._merge(result)
        if self.open and not self.open.closed and sec - self.open.last_second > self.merge_gap_s:
            self.open.closed = True
            self.last_closed_second = self.open.last_second
        return result

    def _suppression(self, sec: int, w10: dict, w30: dict) -> str | None:
        if self.last_raid is not None and 0 <= sec - self.last_raid <= 90:
            return "raid arrived within 90 s (new viewers flood chat)"
        k = w30["kinds"]
        if k.get("giveaway", 0) >= max(3, 0.25 * w30["messages"]):
            return "giveaway / raffle entries"
        if k.get("mod", 0) >= 2 and w10["reaction"] < 3:
            return "moderator announcement"
        if any(0 <= sec - s0 <= 15 for s0 in self.gap_before):
            return "backlog after a chat disconnect / gap"
        if w10["spam"] / max(w10["messages"], 1) > 0.5:
            return "mostly spam / copy-paste (possibly creator-requested)"
        if w10["unique_users"] < self.min_users:
            return f"only {w10['unique_users']} distinct users (flood)"
        if w10["texts"]:
            top_text, n = w10["texts"].most_common(1)[0]
            if len(top_text) > 25 and n >= 0.5 * w10["messages"]:
                return "one repeated message dominates"
        if w10["reaction"] < 2.0:
            return "volume rise without audience-reaction content"
        return None

    def _merge(self, sp: Spike) -> None:
        if self.open and not self.open.closed and sp.second - self.open.last_second <= self.merge_gap_s:
            m = self.open
            m.last_second = sp.second
            m.spikes.append(sp)
            m.reactions.update(sp.metrics.get("top_reactions", {}))
            if sp.score > m.peak_score:
                m.peak_score, m.peak_metrics = sp.score, sp.metrics
            return
        # rate limit new moments
        while self.emitted_times and sp.second - self.emitted_times[0] > 3600:
            self.emitted_times.popleft()
        if len(self.emitted_times) >= self.max_per_hour:
            sp.suppressed, sp.suppress_reason = True, "max candidates per hour reached"
            sp.explanation += " SUPPRESSED: max candidates per hour."
            return
        self.emitted_times.append(sp.second)
        m = Moment(sp.second, sp.second, self.onset(sp.second), sp.score, sp.metrics,
                   Counter(sp.metrics.get("top_reactions", {})), [sp])
        self.open = m
        self.moments.append(m)

    def onset(self, spike_second: int, lookback: int = 25) -> int:
        """First second (going back) where per-second chat rose clearly above baseline:
        the reaction started here, the on-screen event shortly before."""
        base = statistics.median([h[1] for h in self.history]) if self.history else 0.0
        thresh = max(base * 1.8, base + 1.0)
        onset = spike_second
        for s in range(spike_second, spike_second - lookback, -1):
            b = self.buckets.get(s)
            n = b.messages if b else 0
            prev = sum((self.buckets.get(x).messages if self.buckets.get(x) else 0) for x in (s - 1, s - 2)) / 2
            if n >= thresh or prev >= thresh:
                onset = s
            else:
                break
        return onset
