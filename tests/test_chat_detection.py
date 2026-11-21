"""Chat normalization, keywords, spam filtering, spike detection, suppression, merging."""
from studio.chat.normalize import Normalizer, hash_user, normalize_text
from studio.chat.simulator import ChatSimulator, Scenario
from studio.detection import SpikeDetector


def test_normalize_stretched_and_case():
    assert normalize_text("NOOOO WAYYYY!!!") == "no way"
    assert normalize_text("Clippp thaaat") == "clip thaat" or "clip" in normalize_text("Clippp thaaat")
    assert normalize_text("OMGGG") == "omg"


def test_keywords_case_emoji_multilingual_and_custom():
    n = Normalizer(custom_keywords={"leonida drift": 2.0})
    r = n.process("CLIP THAT 😱", "u1", 0)
    assert "CLIP" in r.keywords and "OMG" in r.keywords and r.reaction_weight > 3
    assert "NO WAY" in n.process("no puede ser", "u2", 1).keywords
    assert "INSANE" in n.process("жесть", "u3", 2).keywords
    assert "LEONIDA DRIFT" in n.process("that leonida drift", "u4", 3).keywords
    assert n.process("what car is that", "u5", 4).reaction_weight == 0


def test_spam_link_bot_flood_copypasta():
    n = Normalizer()
    assert n.process("free followers at www.spam.com", "u1", 0).spam_reason == "link"
    assert n.process("CLIP", "bot", 0, is_bot=True).is_spam
    for i in range(6):
        r = n.process(f"lol {i}", "flooder", 10 + i * 0.5)
    assert r.is_spam and r.spam_reason == "user_flood"
    text = "TYPE 1 IF YOU THINK GTA 6 IS THE BEST GAME EVER"
    for i in range(5):
        r = n.process(text, f"c{i}", 30 + i)
    assert r.is_spam and r.spam_reason == "copypasta"
    assert n.process("!enter", "g", 50).kind == "giveaway"


def test_user_hash_is_not_raw_id():
    h = hash_user("twitch", "123456")
    assert "123456" not in h and len(h) == 24 and h == hash_user("twitch", "123456")


def run_feed(scenarios, duration=200, base=3.0, seed=5, **kw):
    sim = ChatSimulator(base_rate=base, users=80, scenarios=scenarios, seed=seed)
    msgs = sim.generate(1_000_000.0, duration)
    det = SpikeDetector(**kw)
    norm = Normalizer()
    fired = []
    last = None
    for m in msgs:
        sec = int(m.ts)
        if last is not None and sec > last:
            fired += det.tick(sec - 1)
        last = sec
        r = norm.process(m.text, hash_user("sim", m.user_id), m.ts, m.is_bot, m.kind if m.kind != "message" else None)
        det.add(m.ts, hash_user("sim", m.user_id), r.reaction_weight, r.emoji_count, r.is_spam, r.kind, r.reactions,
                r.sentiment, r.normalized)
    fired += det.tick(int(1_000_000 + duration) + 60)
    return det, fired


def test_baseline_only_no_spike():
    det, fired = run_feed([], 240)
    assert not [f for f in fired if not f.suppressed]
    assert not det.moments


def test_real_reaction_spike_detected_with_explanation():
    det, fired = run_feed([Scenario(120, 15, "reaction", 25, 140)])
    real = [f for f in fired if not f.suppressed]
    assert real, "spike not detected"
    assert 120 <= real[0].second - 1_000_000 <= 126
    assert "baseline" in real[0].explanation and real[0].metrics["z_rate"] > 3
    assert len(det.moments) == 1  # merged into one candidate, not one per second
    assert det.moments[0].dominant is not None
    assert 118 <= det.moments[0].onset_second - 1_000_000 <= 124


def test_single_keyword_once_is_not_a_spike():
    det, fired = run_feed([Scenario(120, 1.0, "reaction", 2, 2)])
    assert not det.moments


def test_raid_giveaway_bot_copypasta_suppressed():
    for kind, users in (("raid", 90), ("giveaway", 60), ("bot_flood", 3), ("copypasta", 80)):
        det, fired = run_feed([Scenario(120, 10, kind, 25, users)])
        assert not det.moments, f"{kind} produced a candidate"
        assert any(f.suppressed for f in fired), f"{kind} not even flagged"


def test_two_separate_moments_and_rate_limit():
    det, _ = run_feed([Scenario(100, 12, "reaction", 25, 140), Scenario(200, 12, "reaction", 25, 140)], 300)
    assert len(det.moments) == 2
    det, fired = run_feed([Scenario(100, 12, "reaction", 25, 140), Scenario(200, 12, "reaction", 25, 140)], 300,
                          max_per_hour=1)
    assert len(det.moments) == 1
    assert any(f.suppress_reason == "max candidates per hour reached" for f in fired)


def test_backlog_after_gap_suppressed():
    sim = ChatSimulator(base_rate=3, scenarios=[], seed=2)
    msgs = [m for m in sim.generate(2_000_000.0, 200) if not (110 <= m.ts - 2_000_000 < 140)]
    burst = ChatSimulator(base_rate=0.01, scenarios=[Scenario(140, 6, "reaction", 30, 140)], seed=3).generate(2_000_000.0, 150)
    det = SpikeDetector()
    n = Normalizer()
    for m in sorted(msgs + burst, key=lambda x: x.ts):
        r = n.process(m.text, m.user_id, m.ts)
        det.add(m.ts, m.user_id, r.reaction_weight, r.emoji_count, r.is_spam, r.kind, r.reactions)
    fired = det.tick(2_000_260)
    assert not det.moments
    assert any("gap" in (f.suppress_reason or "") for f in fired)
