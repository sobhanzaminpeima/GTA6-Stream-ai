"""Scoring, timing, rights, costs, jobs, subtitles, metadata, classification."""
import datetime as dt
import time

import pytest

from studio import costs, jobs, rights, scoring, subtitles
from studio.db import session, set_automation
from studio.discovery import classify
from studio.models import Job, Livestream, RightsPermission, RightsState, Streamer
from studio.packaging import titles_for
from studio.pipeline import cut_plan


# ---------------------------------------------------------------- scoring + timing
def test_score_full_and_reduced_confidence():
    full = scoring.combine({"chat": 90, "audio": 80, "visual": 70, "narrative": 60, "originality": 50})
    assert full.confidence == "high" and full.overall == pytest.approx(90 * .3 + 80 * .2 + 70 * .2 + 60 * .2 + 50 * .1)
    red = scoring.combine({"chat": 90, "audio": None, "visual": None, "narrative": 60, "originality": 50})
    assert red.confidence == "reduced (chat-only)"
    assert sum(red.weights_used.values()) == pytest.approx(1.0, abs=1e-3)
    assert any("renormalised" in e for e in red.explanation)


def test_tiers():
    assert scoring.tier(95) == "Exceptional candidate" and scoring.tier(80) == "High priority"
    assert scoring.tier(65) == "Review recommended" and scoring.tier(10) == "Low priority"


def test_timestamp_mapping_and_uncertainty():
    maps = [(1000.0 + i * 2, 50.0 + i * 2, 0.6) for i in range(50)]
    ev = scoring.estimate_event_wall(1046.0, 6.0)
    m = scoring.map_to_media(ev, maps)
    assert m.media_ts == pytest.approx(90.0, abs=0.01) and 2.5 < m.uncertainty_s < 4
    assert scoring.map_to_media(ev, []).media_ts is None
    assert scoring.calibrated_latency([5, 7, 6, 30], 6.0) in (6, 7)
    assert scoring.calibrated_latency([5], 6.0) == 6.0


def test_cut_plan_respects_available_media_and_scenes():
    cuts = cut_plan(100, 80, 140, 15, 25, scenes=[83.5])
    labels = {c["label"]: c for c in cuts}
    assert set(labels) == {"A", "B", "C"}
    assert labels["B"]["in"] == 83.5  # snapped back to the scene start (<= 3 s)
    assert labels["C"]["in"] == 83.5 and labels["C"]["out"] == 135  # 50 s plan + 1.5 s snapped lead-in
    assert labels["A"]["duration"] == pytest.approx(20.0)
    short = cut_plan(100, 95, 112, 15, 25)
    assert [c["label"] for c in short] == ["A"] or short == []  # never stretched beyond the footage


# ---------------------------------------------------------------- rights
def _stream(s, url, streamer_id=None):
    ls = Livestream(platform="youtube", stream_id=url[-8:] + str(time.time()), source_url=url, channel_name="X", streamer_id=streamer_id)
    s.add(ls)
    s.flush()
    return ls


def test_rights_states():
    with session() as s:
        ls = _stream(s, "https://youtube.com/watch?v=unknown1")
        d = rights.decide(s, ls)
        assert d.state == "UNKNOWN" and not d.can_record and not d.can_edit
        with pytest.raises(rights.RightsError):
            rights.require_capture(s, ls)

        ls2 = _stream(s, "https://youtube.com/watch?v=restrict")
        s.add(RightsPermission(source_url=ls2.source_url, state="RESTRICTED", evidence="creator said no"))
        s.flush()
        assert not rights.decide(s, ls2).can_record

        ls3 = _stream(s, "https://youtube.com/watch?v=noevid01")
        s.add(RightsPermission(source_url=ls3.source_url, state="AUTHORIZED_FOR_CLIPPING", recording_allowed=True, editing_allowed=True))
        s.flush()
        d3 = rights.decide(s, ls3)
        assert not d3.can_record and "no authorization evidence recorded" in d3.reasons

        ls4 = _stream(s, "https://youtube.com/watch?v=expired1")
        s.add(RightsPermission(source_url=ls4.source_url, state="EXPLICITLY_LICENSED", evidence="contract.pdf", recording_allowed=True,
                               editing_allowed=True, expires_at=dt.datetime(2020, 1, 1)))
        s.flush()
        assert rights.decide(s, ls4).state == "UNKNOWN"

        st = Streamer(platform="twitch", channel_id="t-123", name="Licensor")
        s.add(st)
        s.flush()
        ls5 = _stream(s, "https://twitch.tv/licensor", st.id)
        s.add(RightsPermission(streamer_id=st.id, state="AUTHORIZED_FOR_CLIPPING", evidence="https://campaign/terms",
                               recording_allowed=True, editing_allowed=True, commercial_use=False, attribution_required="credit @Licensor"))
        s.flush()
        d5 = rights.decide(s, ls5)
        assert d5.can_record and d5.can_edit and d5.can_publish_youtube and not d5.commercial_use
        assert "non-commercial only: do not monetize" in d5.reasons and d5.attribution == "credit @Licensor"


def test_reuse_risk_separate_from_rights():
    d = rights.RightsDecision("AUTHORIZED_FOR_CLIPPING", True, True, True, True, None, 1)
    assert rights.reuse_risk(d, False, 0.9)["risk"] == "high"
    own = rights.RightsDecision("OWNED", True, True, True, True, None, 1)
    assert rights.reuse_risk(own, True, 0.3)["risk"] == "low"


# ---------------------------------------------------------------- costs
def test_budget_gate():
    with session() as s:
        set_automation(s, {"allow_paid_llm": False, "daily_budget_usd": 1.0})
    with session() as s:
        with pytest.raises(costs.PaidCallNotAllowed):
            costs.check_budget(s, "anthropic", 0.01)
        set_automation(s, {"allow_paid_llm": True})
        costs.check_budget(s, "anthropic", 0.5)
        costs.record(s, "anthropic", "messages.create", 1000, "tokens", 0.9, project_id=None)
    with session() as s:
        with pytest.raises(costs.BudgetExceeded):
            costs.check_budget(s, "anthropic", 0.2)
        set_automation(s, {"allow_paid_llm": False, "daily_budget_usd": 3.0})
    assert costs.llm_cost("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)


def test_youtube_quota_guard():
    with session() as s:
        costs.record(s, "youtube_data", "search.list", 9_600)
    with session() as s:
        with pytest.raises(costs.BudgetExceeded):
            costs.check_quota(s, 101)
        from studio.models import ApiUsage
        s.query(ApiUsage).filter(ApiUsage.provider == "youtube_data").delete()


# ---------------------------------------------------------------- jobs
def test_job_retry_then_success_and_cancel():
    calls = {"n": 0}

    @jobs.handler("flaky_test")
    def flaky(payload, ctx):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        ctx.progress(0.5, "half")
        return {"ok": True}

    jid = jobs.enqueue("flaky_test", {}, max_attempts=3)
    with session() as s:  # skip back-off wait in the test
        s.get(Job, jid).run_after = 0
    jobs.run_one(jobs._claim("t"))
    with session() as s:
        j = s.get(Job, jid)
        assert j.status == "queued" and "transient" in j.error
        j.run_after = 0
    jobs.run_one(jobs._claim("t"))
    with session() as s:
        assert s.get(Job, jid).status == "done"

    jid2 = jobs.enqueue("flaky_test", {})
    jobs.cancel(jid2)
    with session() as s:
        assert s.get(Job, jid2).status == "cancelled"


def test_crash_recovery_requeues_stale_running_job():
    @jobs.handler("noop_test")
    def noop(payload, ctx):
        return {}
    jid = jobs.enqueue("noop_test", {})
    with session() as s:
        j = s.get(Job, jid)
        j.status, j.heartbeat, j.locked_by = "running", time.time() - 999, "dead-worker"
    claimed = jobs._claim("new-worker")
    assert claimed == jid
    jobs.run_one(claimed)
    with session() as s:
        assert s.get(Job, jid).status == "done"


# ---------------------------------------------------------------- captions + metadata + classification
def test_caption_chunking_never_splits_gta_number():
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25} for i, w in enumerate("This new GTA 6 moment made chat eight times faster".split())]
    cues = subtitles.chunk(words, max_words=3)
    for c in cues:
        assert c["words"][-1].upper() != "GTA", cues
    srt = subtitles.to_srt(cues)
    assert subtitles.parse_srt(srt)[0]["words"] == cues[0]["words"]
    ass = subtitles.to_ass(cues)
    assert "PlayResY: 1920" in ass and "\\c&H004DE1FF" in ass  # yellow emphasis
    assert subtitles.safe_zone_ok({"margin_v": 560})["ok"]
    assert not subtitles.safe_zone_ok({"margin_v": 200})["ok"]


def test_classification_is_conservative():
    assert classify("GTA 6 gameplay first look!")[0] == "unverified"
    assert classify("Anything", category="Grand Theft Auto VI")[0] == "confirmed_gameplay"
    assert classify("GTA RP NoPixel heist night")[0] == "gta_v_gameplay"
    assert classify("GTA 6 trailer 3 reaction")[0] == "trailer_reaction"
    assert classify("GTA 6 in Unreal Engine 5 concept")[0] == "fan_concept"
    assert classify("GTA 6 news and leaks discussion")[0] == "gta_vi_commentary"


def test_titles_never_call_gta5_footage_gta6():
    t5 = titles_for("GTA 5", "CLIP", 8, "SURPRISE")
    assert all("GTA 6" not in t for t in t5) and all(len(t) <= 100 for t in t5)
    assert len(titles_for("GTA 6", "HOW", 5, "EDUCATIONAL")) == 5
