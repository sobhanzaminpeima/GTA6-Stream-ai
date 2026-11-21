"""Synthetic chat for demos and tests (clearly labelled SIMULATION in the UI).

A Poisson baseline of ordinary chatter plus scheduled scenarios: a real reaction
spike, a raid, a giveaway, a bot flood and a creator-requested copy-paste.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Iterator

from . import RawMessage

NEUTRAL = ["hi chat", "where is this", "what car is that", "first time here", "love the stream", "what mission is this",
           "is this the new update", "music is good", "how long have you been live", "gg", "nice", "chat is slow today",
           "what graphics settings", "anyone else lagging", "this map is huge", "go left", "what time is it there",
           "hello from germany", "w stream", "do the heist next", "that sunset tho", "the radio slaps", "true"]
REACTION = ["CLIP THAT", "NO WAY", "OMG", "clip it", "WHAT JUST HAPPENED", "how did he do that", "REPLAY", "INSANE",
            "😱😱😱", "🤯", "THIS IS WILD", "no wayyyy", "LMAO", "did you see that??", "clip clip clip", "bro WHAT",
            "omg omg", "run it back", "💀💀", "CRAZY"]


@dataclass
class Scenario:
    at: float            # seconds after start
    duration: float
    kind: str            # reaction | raid | giveaway | bot_flood | copypasta
    rate: float = 25.0   # messages per second during the scenario
    users: int = 120


@dataclass
class ChatSimulator:
    base_rate: float = 3.0
    users: int = 80
    scenarios: list[Scenario] = field(default_factory=list)
    seed: int = 7
    platform: str = "simulation"

    def generate(self, start: float, duration: float) -> list[RawMessage]:
        """All messages for [start, start+duration) in timestamp order (offline use)."""
        rnd = random.Random(self.seed)
        out: list[RawMessage] = []
        n = 0

        def emit(ts, user, text, bot=False, kind="message"):
            nonlocal n
            n += 1
            out.append(RawMessage(self.platform, f"sim-{self.seed}-{n}", ts, user, text, bot, kind))

        t = 0.0
        while t < duration:
            t += rnd.expovariate(self.base_rate)
            if t < duration:
                emit(start + t, f"u{rnd.randrange(self.users)}", rnd.choice(NEUTRAL))
        for sc in self.scenarios:
            if sc.kind == "raid":
                emit(start + sc.at, "raid", f"RAID from SomeStreamer ({sc.users} viewers)", kind="raid")
            t = 0.0
            while t < sc.duration:
                # reactions ramp up fast and decay: realistic shape around the event
                prog = t / sc.duration
                shape = min(1.0, prog * 6) * (1.0 - 0.6 * prog)
                t += rnd.expovariate(max(sc.rate * shape, 0.5))
                ts = start + sc.at + t
                if sc.kind == "reaction":
                    emit(ts, f"r{rnd.randrange(sc.users)}", rnd.choice(REACTION))
                elif sc.kind == "raid":
                    emit(ts, f"raider{rnd.randrange(sc.users)}", rnd.choice(["raid hype", "HEYYY", "raided!", "hi from the raid", "LETS GO"]))
                elif sc.kind == "giveaway":
                    emit(ts, f"g{rnd.randrange(sc.users)}", rnd.choice(["!enter", "!enter pls", "!join", "!enter 🙏"]), kind="giveaway")
                elif sc.kind == "bot_flood":
                    emit(ts, f"bot{rnd.randrange(3)}", "FREE followers at cheap-viewers dot com", True)
                elif sc.kind == "copypasta":
                    emit(ts, f"c{rnd.randrange(sc.users)}", "TYPE 1 IF YOU THINK GTA 6 IS THE BEST GAME EVER MADE IN HISTORY")
        out.sort(key=lambda m: m.ts)
        return out

    def stream(self, duration: float, stop=lambda: False, start: float | None = None) -> Iterator[RawMessage]:
        """Real-time feed (live demo): yields each message when its timestamp arrives."""
        start = start if start is not None else time.time()
        for m in self.generate(start, duration):
            while time.time() < m.ts:
                if stop():
                    return
                time.sleep(max(0.0, min(0.05, m.ts - time.time())))
            if stop():
                return
            yield m
