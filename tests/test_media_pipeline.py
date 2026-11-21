"""Rolling buffer, retention, extraction + A/V sync, signals, rights-blocked monitoring,
publishing approval/idempotency/resumable upload, and a full render end-to-end."""
import json
import pathlib
import time

import httpx
import pytest
from sqlalchemy import select

from studio import jobs, media, monitor, pipeline, publishing
from studio.chat.simulator import ChatSimulator, Scenario
from studio.db import session, set_automation
from studio.models import (ClipProject, Livestream, MediaSegment, MonitoringSession, RightsPermission, UploadJob, VideoExport,
                           ViralCandidate)


def test_signals_detect_cut_and_loud_burst(sample_video):
    v = media.visual_features(str(sample_video), fps=4)
    assert any(7.0 <= c <= 9.0 for c in v["cuts"]), v["cuts"]
    quiet = media.audio_features(media.decode_audio(str(sample_video), ss=1, t=5))
    loud = media.audio_features(media.decode_audio(str(sample_video), ss=6, t=5))
    assert loud["peak_jump_db"] > quiet["peak_jump_db"] + 5
    assert media.audio_excitement(loud)[0] > media.audio_excitement(quiet)[0]


def test_rolling_buffer_retention_and_extraction(sample_video, tmpdir_session):
    segs = []
    buf = media.RollingBuffer(9001, str(sample_video), seg_seconds=2.0, realtime=True, on_segment=segs.append)
    buf.start()
    buf.thread.join(40)
    assert buf.ended and len(segs) >= 8
    starts = [s.media_start for s in segs]
    assert starts == sorted(starts) and abs(starts[1] - starts[0] - 2.0) < 0.2
    assert abs(sum(s.duration for s in segs) - 18.0) < 0.5
    # extraction across segment boundaries, frame-accurate, audio in sync
    out = tmpdir_session / "cut.mp4"
    seg_dicts = [{"path": s.path, "media_start": s.media_start, "duration": s.duration} for s in segs]
    r = media.extract_clip(seg_dicts, 5.0, 13.0, out)
    info = media.probe(out)
    assert abs(info["duration"] - 8.0) < 0.15 and info["vcodec"] == "h264" and info["acodec"] == "aac"
    a = media.decode_audio(str(out))
    assert abs(len(a) / 16000 - 8.0) < 0.12  # audio length == video length (sync)
    cut_seen = media.visual_features(str(out), fps=4)["cuts"]
    assert any(2.5 <= c <= 3.5 for c in cut_seen), cut_seen  # source cut at 8 s -> 3 s into the clip
    with pytest.raises(ValueError):
        media.extract_clip(seg_dicts, 100, 110, tmpdir_session / "none.mp4")


def _owned_stream(s, key, owned=True, media_source=None):
    ls = Livestream(platform="simulation", stream_id=key, title=f"test {key}", channel_name="Test", source_url=f"sim://{key}",
                    is_simulation=True, media_source=media_source, content_type="gta_vi_commentary")
    s.add(ls)
    s.flush()
    if owned:
        s.add(RightsPermission(source_url=ls.source_url, state="OWNED", content_owner="GTA VI Central", evidence="own render",
                               recording_allowed=True, editing_allowed=True, commercial_use=True))
    return ls


def test_unauthorized_stream_never_records_media(sample_video):
    """Recording requested on an UNKNOWN-rights stream: chat runs, no buffer, no files, report only."""
    with session() as s:
        ls = _owned_stream(s, "unauth-1", owned=False, media_source=str(sample_video))
        lid = ls.id
    sim = ChatSimulator(base_rate=4, scenarios=[], seed=1)
    sim.scenarios = []
    sid = monitor.start_monitoring(lid, record_media=True, sim=sim)
    time.sleep(3)
    rt = monitor.RUNTIMES[sid]
    assert rt.buffer is None
    monitor.stop_monitoring(sid)
    with session() as s:
        ms = s.get(MonitoringSession, sid)
        assert not ms.buffer_enabled and "media restricted" in (ms.status_detail or "")
        assert not s.scalars(select(MediaSegment).where(MediaSegment.session_id == sid)).all()
        from studio.models import AuditLog
        assert s.scalar(select(AuditLog).where(AuditLog.action == "media_capture_blocked"))
    from studio.config import settings
    assert not (settings.data_dir / "buffers" / f"session_{sid}").exists()


def test_retention_keeps_pinned_segments(sample_video, tmpdir_session):
    with session() as s:
        ls = _owned_stream(s, "ret-1", media_source=str(sample_video))
        ms = MonitoringSession(livestream_id=ls.id, status="MONITORING")
        s.add(ms)
        s.flush()
        sid = ms.id
        d = tmpdir_session / "ret"
        d.mkdir(exist_ok=True)
        old = time.time() - 3600
        for i in range(3):
            f = d / f"seg_{i}.ts"
            f.write_bytes(b"x")
            s.add(MediaSegment(session_id=sid, seq=i, path=str(f), media_start=i * 2, duration=2, wall_start=old + i * 2,
                               pinned_by=["cand1"] if i == 1 else []))
    rt = monitor.MonitorRuntime(sid, lambda st, on: iter(()), None, {"spike_sensitivity": 3.5, "min_messages_per_window": 8,
                                                                    "merge_gap_s": 45, "max_candidates_per_hour": 6, "buffer_minutes": 10})
    rt.buffer = object()  # retention only runs when a buffer exists
    rt._retention()
    with session() as s:
        segs = {x.seq: x for x in s.scalars(select(MediaSegment).where(MediaSegment.session_id == sid))}
        assert segs[0].deleted and segs[2].deleted and not segs[1].deleted
        assert not pathlib.Path(segs[0].path).exists() and pathlib.Path(segs[1].path).exists()


def test_generate_blocked_without_rights():
    from fastapi.testclient import TestClient
    from studio.api import app
    with session() as s:
        ls = _owned_stream(s, "norights-gen", owned=False)
        ms = MonitoringSession(livestream_id=ls.id, status="STOPPED")
        s.add(ms)
        s.flush()
        c = ViralCandidate(session_id=ms.id, livestream_id=ls.id, dedupe_key="norights-gen", event_ts=1, first_spike_ts=1,
                           last_spike_ts=1, clip_readiness="report_only")
        s.add(c)
        s.flush()
        cid = c.id
    client = TestClient(app)
    r = client.post(f"/api/candidates/{cid}/generate", json={"preset": "CINEMATIC"})
    assert r.status_code == 403 and "permission" in r.json()["detail"]
    rep = client.get(f"/api/candidates/{cid}/report").json()
    assert rep["media_acquired"] is False and "May I use" in rep["permission_request_draft"]


@pytest.fixture(scope="module")
def rendered_project(sample_video, tmpdir_session):
    """Full pipeline on an owned upload: extract -> 14 stages -> MP4 (silent stub TTS = no paid calls)."""
    with session() as s:
        set_automation(s, {"narrator_preset": {"provider": "silent_stub"}, "pre_roll_s": 7, "post_roll_s": 8})
    longer = tmpdir_session / "owned_long.mp4"
    media.run(["-stream_loop", "2", "-i", str(sample_video), "-c", "copy", str(longer)])  # 54 s of our own test footage
    from studio.demo import register_upload
    up = register_upload(longer, "Test upload", "GTA VI Central", "OWNED", "", 26.0, "gta_vi_commentary")
    jobs.drain(120)
    with session() as s:
        c = s.get(ViralCandidate, up["candidate_id"])
        assert c.clip_readiness == "clip_ready", c.explanation
        assert c.confidence != "high"  # no chat for uploads -> never claims full confidence
    pid = pipeline.create_project(up["candidate_id"], "CINEMATIC", "B")
    jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=1)
    jobs.drain(600)
    return pid


def test_full_render_produces_valid_short(rendered_project):
    with session() as s:
        p = s.get(ClipProject, rendered_project)
        from studio.models import Job
        errs = [j.error for j in s.scalars(select(Job).where(Job.status == "failed"))]
        assert p.status == "ready_for_approval", (p.status, p.stage, errs[-1:] if errs else None, p.qc)
        q = p.qc
        assert q["checks"]["resolution_1080x1920"] and q["checks"]["codec_h264"] and q["checks"]["audio_aac"]
        assert q["checks"]["captions_in_safe_zone"] and q["checks"]["claims_have_evidence"]
        assert q["lufs"] is not None and abs(q["lufs"] + 14) < 2.0
        folder = pathlib.Path(p.folder)
        for f in ("short.mp4", "cover.jpg", "thumbnail.jpg", "captions.srt", "captions.ass", "metadata.json", "contact_sheet.png"):
            assert (folder / f).exists(), f
        md = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        assert len(md["title_options"]) == 5 and md["default_privacy"] == "private" and md["hashtags"]
        info = media.probe(folder / "short.mp4")
        assert info["width"] == 1080 and info["height"] == 1920 and 15 <= info["duration"] <= 60
        assert media.decode_audio(str(folder / "short.mp4")).size > 16000 * 10


def test_stage_regeneration_keeps_earlier_work(rendered_project):
    pipeline.regenerate(rendered_project, "cover")
    with session() as s:
        st = s.get(ClipProject, rendered_project).stages
        assert "render" in st and "cover" not in st and "qc" not in st
    jobs.enqueue("generate_short", {"project_id": rendered_project}, max_attempts=1)
    jobs.drain(300)
    with session() as s:
        assert s.get(ClipProject, rendered_project).status == "ready_for_approval"


def test_approval_gate_idempotency_and_resumable_upload(rendered_project, monkeypatch):
    with session() as s:
        ex = s.scalar(select(VideoExport).where(VideoExport.project_id == rendered_project).order_by(VideoExport.id.desc()))
        eid = ex.id
    with pytest.raises(publishing.ApprovalRequired):
        publishing.prepare_package(eid)
    publishing.approve(eid, "owner", "approved", "private")
    with pytest.raises(publishing.ApprovalRequired):
        publishing.prepare_package(eid, privacy="public")  # approval was for private only
    a = publishing.prepare_package(eid)
    b = publishing.prepare_package(eid)
    assert a["upload_job_id"] == b["upload_job_id"]  # same file + channel -> same job

    state = {"chunks": 0, "init": 0}

    def handler(req: httpx.Request):
        if "uploadType=resumable" in str(req.url):
            state["init"] += 1
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private"
            return httpx.Response(200, headers={"Location": "https://upload.example/session1"})
        if str(req.url).startswith("https://upload.example/session1"):
            state["chunks"] += 1
            rng = req.headers["Content-Range"]
            end = int(rng.split("-")[1].split("/")[0])
            total = int(rng.split("/")[1])
            if end + 1 < total:
                return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})
            return httpx.Response(200, json={"id": "VIDEO123"})
        if "thumbnails/set" in str(req.url):
            return httpx.Response(200, json={})
        return httpx.Response(404)
    monkeypatch.setattr(publishing, "youtube_oauth_token", lambda: "tok")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    r = publishing.upload(a["upload_job_id"], http=http, chunk=256 * 1024)
    assert r["youtube_video_id"] == "VIDEO123" and state["chunks"] >= 2 and state["init"] == 1
    r2 = publishing.upload(a["upload_job_id"], http=http)
    assert r2["duplicate"] and state["init"] == 1  # no second upload
    with session() as s:
        assert s.get(UploadJob, a["upload_job_id"]).status == "uploaded"
        from studio.models import PublishedVideo
        pv = s.scalar(select(PublishedVideo).where(PublishedVideo.youtube_video_id == "VIDEO123"))
        assert pv.attributes["editing_preset"] == "CINEMATIC"


def test_growth_report_labels_small_samples():
    from studio.growth import weekly_report
    from studio.models import AnalyticsSnapshot
    with session() as s:
        s.add(AnalyticsSnapshot(youtube_video_id="VIDEO123", day="2026-11-20", metrics={"views": 120, "subscribersGained": 2}))
    r = weekly_report("2026-W47")
    assert r["with_analytics"] == 1 and "hypothesis" in r["data_note"]
    assert all("HYPOTHESIS" in g["confidence"] for g in r["by_preset"])
