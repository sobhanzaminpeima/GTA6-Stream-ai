"""End-to-end demonstration and simulation helpers.

Authorized sample footage: GTA VI Central's OWN long-form render
(long/2026-11-21-gta6-vs-gta5/video.mp4, original graphics, our narration) streamed
in real time through the rolling buffer as if it were a live broadcast. Chat is
synthetic and every output is labelled SIMULATED / DEMO. A second simulated stream
with UNKNOWN rights runs alongside to prove the app records nothing for it.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

from sqlalchemy import select

from . import jobs, media, pipeline, publishing, rights
from .chat.simulator import ChatSimulator, Scenario
from .config import ROOT, settings
from .db import audit, migrate, session, set_automation
from .models import (Livestream, MediaSegment, MonitoringSession, MonitorStatus, RightsPermission, RightsState, ViralCandidate,
                     VideoExport, now)

DEMO_SOURCE = ROOT / "long" / "2026-11-21-gta6-vs-gta5" / "video.mp4"
EVENT_MEDIA_S = 92.0      # where the scripted "moment" sits in the source
VIEWER_DELAY_S = 6.0      # simulated broadcast delay (3 s) + human reaction (3 s)


def demo_source() -> pathlib.Path:
    """The owner's own render when present; otherwise a generated synthetic sample feed
    (gradient + tone, with a visual cut and a louder burst at the scripted moment) so the
    simulation works on any machine without third-party footage."""
    if DEMO_SOURCE.exists():
        return DEMO_SOURCE
    out = settings.data_dir / "sample_feed.mp4"
    if out.exists() and out.stat().st_size > 100_000:
        return out
    ev = EVENT_MEDIA_S
    media.run([
        "-f", "lavfi", "-i", f"gradients=s=1280x720:r=30:speed=0.015:d={ev:.0f}:c0=0x1a0f33:c1=0x7a2bff:c2=0xff2e97:c3=0x09070f",
        "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=12",
        "-f", "lavfi", "-i", f"gradients=s=1280x720:r=30:speed=0.02:d={170 - ev - 12:.0f}:c0=0xff8c28:c1=0x7a2bff",
        "-f", "lavfi", "-i", f"sine=f=220:r=48000:d={ev:.0f}", "-f", "lavfi", "-i", "sine=f=880:r=48000:d=12",
        "-f", "lavfi", "-i", f"sine=f=330:r=48000:d={170 - ev - 12:.0f}",
        "-filter_complex", "[3:a]volume=0.05[a0];[4:a]volume=0.7[a1];[5:a]volume=0.05[a2];"
                           "[0:v][a0][1:v][a1][2:v][a2]concat=n=3:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", str(out)], timeout=900)
    return out


def demo_scenarios(event_chat_at: float) -> list[Scenario]:
    return [Scenario(62, 6, "giveaway", 9, 60),          # suppressed: giveaway entries
            Scenario(76, 4, "bot_flood", 14, 3),          # suppressed: spam / few users
            Scenario(event_chat_at, 16, "reaction", 26, 140),  # the real moment
            Scenario(event_chat_at + 30, 9, "raid", 22, 90)]   # suppressed: raid


def _sim_stream(s, title: str, channel: str, key: str, source: str | None, owned: bool) -> Livestream:
    ls = s.scalar(select(Livestream).where(Livestream.platform == "simulation", Livestream.stream_id == key))
    if ls is None:
        ls = Livestream(platform="simulation", stream_id=key, is_simulation=True)
        s.add(ls)
    ls.title, ls.channel_name, ls.source_url = title, channel, f"sim://{key}"
    ls.is_live, ls.chat_available, ls.language, ls.viewer_count = True, True, "en", 1800 if owned else 5200
    ls.content_type = "gta_vi_commentary" if owned else "unverified"
    ls.classification_reason = "owner verified (demo)" if owned else "simulated third-party stream"
    ls.media_source = source if owned else "sim://third-party-feed"  # never used: rights block it
    ls.started_at = now()
    s.flush()
    if owned and not s.scalar(select(RightsPermission).where(RightsPermission.source_url == ls.source_url)):
        s.add(RightsPermission(source_url=ls.source_url, state=RightsState.OWNED.value, content_owner=settings.channel_name,
                               evidence=f"own file used for the simulation: {pathlib.Path(source).name}",
                               commercial_use=True, editing_allowed=True, recording_allowed=True, platforms_allowed=["youtube"]))
    return ls


def start_simulation(record_media: bool = True, source: str | None = None, restricted: bool = False) -> dict:
    """Used by the dashboard's 'Run simulation' button."""
    from .monitor import start_monitoring
    src = source or str(demo_source())
    if not restricted and not pathlib.Path(src).exists():
        raise FileNotFoundError(src)
    with session() as s:
        if restricted:
            ls = _sim_stream(s, "[SIM] Third-party GTA 6 stream (no permission)", "SomeStreamer (simulated)", "sim-thirdparty", None, False)
        else:
            own = pathlib.Path(src) == DEMO_SOURCE
            ls = _sim_stream(s, "[SIM] GTA 6 vs GTA 5 - our own broadcast" if own else "[SIM] Synthetic sample feed (owned test broadcast)",
                             settings.channel_name, "sim-owned", src, True)
            if not own:
                ls.content_type, ls.classification_reason = "unverified", "synthetic test video (not game footage)"
        lid = ls.id
    sim = ChatSimulator(base_rate=3.0, users=80, scenarios=demo_scenarios(EVENT_MEDIA_S + VIEWER_DELAY_S),
                        seed=11 if restricted else 7)
    sid = start_monitoring(lid, record_media=record_media, sim=sim)
    return {"livestream_id": lid, "session_id": sid, "expected_event_media_s": EVENT_MEDIA_S,
            "note": "chat is simulated; the moment's reactions start ~98 s in"}


def register_upload(path: pathlib.Path, title: str, owner: str, state: str, evidence: str, moment_at: float | None,
                    content_type: str) -> dict:
    """Owner-provided recording or licensed clip -> candidate + extraction job."""
    info = media.probe(path)
    if not info.get("duration"):
        raise ValueError("not a readable video")
    h = hashlib.sha256(path.read_bytes()[: 8 << 20]).hexdigest()[:16]
    how = "given by owner"
    if moment_at is None:  # strongest combined audio-jump + motion second
        x = media.audio_features(media.decode_audio(str(path)), frame_s=0.5)
        v = media.visual_features(str(path), fps=2.0)
        db = x.get("rms_db") or [0]
        mo = v.get("motion") or [0]
        n = min(len(db), len(mo))
        if n > 4:
            import numpy as np
            a = np.array(db[:n]) - np.median(db[:n])
            m = np.array(mo[:n]) / (np.max(mo[:n]) or 1)
            moment_at = float(np.argmax(a / (np.std(a) or 1) + 2 * m) * 0.5)
            how = "detected: loudest + most active second"
        else:
            moment_at = info["duration"] / 2
            how = "middle of the clip (too short to analyse)"
    with session() as s:
        ls = Livestream(platform="upload", stream_id=h, title=title, channel_name=owner, source_url=f"upload://{path.name}",
                        media_source=str(path), is_live=False, chat_available=False, content_type=content_type,
                        classification_reason="owner verified (upload)")
        s.add(ls)
        s.flush()
        s.add(RightsPermission(source_url=ls.source_url, state=state, content_owner=owner, evidence=evidence or "owner's own recording",
                               commercial_use=True, editing_allowed=True, recording_allowed=True, platforms_allowed=["youtube"]))
        ms = MonitoringSession(livestream_id=ls.id, status=MonitorStatus.STOPPED.value, chat_enabled=False, status_detail="upload")
        s.add(ms)
        s.flush()
        c = ViralCandidate(session_id=ms.id, livestream_id=ls.id, dedupe_key=f"upload:{h}:{int(moment_at)}", event_ts=time.time(),
                           first_spike_ts=time.time(), last_spike_ts=time.time(), media_ts=moment_at, media_ts_uncertainty=0.0,
                           source_url=ls.source_url, description=f"Uploaded clip; moment at {moment_at:.1f}s ({how}).",
                           chat_score=0.0, narrative_score=50.0, originality_score=80.0, overall_score=0.0,
                           confidence="no chat (upload)", rights_state=state, clip_readiness="extracting",
                           explanation={"timing": {"method": how}, "no_chat": True})
        s.add(c)
        s.flush()
        cid = c.id
        audit(s, "source_uploaded", f"candidate:{cid}", "owner", "security", state=state, file=path.name)
    jid = jobs.enqueue("extract_clip", {"candidate_id": cid})
    return {"candidate_id": cid, "job_id": jid, "moment_at": moment_at, "how": how}


# ---------------------------------------------------------------- CLI end-to-end demo
def run_demo(out_json: pathlib.Path | None = None, tts: str = "windows_sapi") -> dict:
    """Live, real-time end-to-end run (~3 min). Returns a verification report."""
    from .monitor import RUNTIMES, start_monitoring, stop_monitoring
    migrate()
    t_start = time.time()
    report: dict = {"steps": []}

    def step(name, ok, **info):
        report["steps"].append({"step": name, "ok": bool(ok), **info})
        print(f"[{'OK ' if ok else 'FAIL'}] {name} {json.dumps(info, default=str)[:300]}")

    with session() as s:
        set_automation(s, {"narrator_preset": {"provider": "elevenlabs" if tts == "elevenlabs" else tts, "voice": "Brian", "speed": 1.05},
                           "auto_generate_drafts": False})
    demo_source()  # own render, or generate the synthetic sample feed
    pool = jobs.WorkerPool(2)
    pool.start()
    owned = start_simulation(record_media=True)
    third = start_simulation(record_media=True, restricted=True)  # asks for recording; must be refused
    step("1-2 monitoring started; authorized rolling buffer running", True, owned=owned, restricted=third)
    with session() as s:
        ms_r = s.get(MonitoringSession, third["session_id"])
        step("restricted stream: media capture refused, chat-only", not ms_r.buffer_enabled,
             status=ms_r.status, detail=(ms_r.status_detail or "")[:160])
    # wait for the live run: chat + buffer + post-roll + extraction
    deadline = time.time() + 330
    cand = None
    while time.time() < deadline:
        with session() as s:
            cand = s.scalar(select(ViralCandidate).where(ViralCandidate.session_id == owned["session_id"])
                            .order_by(ViralCandidate.overall_score.desc()))
            ready = cand is not None and cand.clip_readiness == "clip_ready"
        if ready:
            break
        time.sleep(2)
    for sid in (owned["session_id"], third["session_id"]):
        rt = RUNTIMES.get(sid)
        if rt and rt.thread:
            rt.thread.join(60)
    with session() as s:
        from .models import DetectedSpike
        spikes = s.scalars(select(DetectedSpike).where(DetectedSpike.session_id == owned["session_id"])).all()
        sup = sorted({sp.suppress_reason for sp in spikes if sp.suppressed})
        cands = s.scalars(select(ViralCandidate).where(ViralCandidate.session_id == owned["session_id"])).all()
        step("3-5 normal chat ingested, spike injected and detected", any(not sp.suppressed for sp in spikes),
             spikes=len(spikes), real=sum(not sp.suppressed for sp in spikes), suppressed_reasons=sup)
        step("duplicate prevention: one candidate for the merged moment", len(cands) == 1, candidates=len(cands))
        segs = s.scalars(select(MediaSegment).where(MediaSegment.session_id == owned["session_id"])).all()
        step("rolling buffer segments recorded", len(segs) > 20, segments=len(segs))
        restricted_dir = settings.data_dir / "buffers" / f"session_{third['session_id']}"
        r_segs = s.scalars(select(MediaSegment).where(MediaSegment.session_id == third["session_id"])).all()
        r_cands = s.scalars(select(ViralCandidate).where(ViralCandidate.session_id == third["session_id"])).all()
        step("restricted stream: no media downloaded, timestamped report instead",
             not restricted_dir.exists() and not r_segs and all(c.clip_readiness == "report_only" for c in r_cands),
             candidates=len(r_cands), segments=len(r_segs), buffer_dir_exists=restricted_dir.exists())
        if cand is None:
            step("candidate created", False)
            pool.stop()
            return report
        cand = s.get(ViralCandidate, cand.id)
        err = abs((cand.media_ts or 0) - EVENT_MEDIA_S)
        step("6 candidate scored", cand.overall_score > 0, score=cand.overall_score, tier=cand.tier, confidence=cand.confidence,
             components={"chat": cand.chat_score, "audio": cand.audio_score, "visual": cand.visual_score,
                         "narrative": cand.narrative_score, "originality": cand.originality_score})
        step("7 chat timestamp mapped to video time", err <= (cand.media_ts_uncertainty or 0) + 1.5,
             mapped_media_s=cand.media_ts, true_media_s=EVENT_MEDIA_S, error_s=round(err, 2), uncertainty_s=cand.media_ts_uncertainty)
        step("8 correct section extracted from the buffer", cand.clip_readiness == "clip_ready",
             cuts=(cand.explanation or {}).get("media", {}).get("cuts"))
        cid = cand.id
    pid = pipeline.create_project(cid, "CINEMATIC", "B")
    jid = jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=1)
    while True:
        with session() as s:
            from .models import ClipProject, Job
            j = s.get(Job, jid)
            if j.status in ("done", "failed", "cancelled"):
                break
        time.sleep(2)
    pool.stop()
    with session() as s:
        from .models import ClipProject
        p = s.get(ClipProject, pid)
        st = p.stages or {}
        step("9 commentary script generated", "commentary" in st, provider=st.get("commentary", {}).get("output", {}).get("provider"),
             hooks=st.get("commentary", {}).get("output", {}).get("hookscore"))
        step("10 narration generated", "narration" in st, provider=st.get("narration", {}).get("output", {}).get("provider"),
             timing=st.get("narration", {}).get("output", {}).get("timing"))
        step("11 subtitles created", "captions" in st, **{k: v for k, v in st.get("captions", {}).get("output", {}).items() if k in ("cues", "safe_zone")})
        q = p.qc or {}
        step("12 vertical MP4 rendered", "render" in st and q.get("checks", {}).get("resolution_1080x1920"), qc=q.get("checks"),
             lufs=q.get("lufs"), path=st.get("render", {}).get("output", {}).get("path"))
        step("13 thumbnail/cover generated", "cover" in st, **st.get("cover", {}).get("output", {}))
        step("14 YouTube metadata generated", "metadata" in st, title=p.title)
        step("15 preview ready (dashboard: AI Clip Studio)", p.status == "ready_for_approval", status=p.status, job_error=(j.error or "")[:400])
        ex = s.scalar(select(VideoExport).where(VideoExport.project_id == pid))
        eid = ex.id if ex else None
    if eid and p.status == "ready_for_approval":
        try:
            publishing.prepare_package(eid)
            step("16 upload blocked before approval", False)
        except publishing.ApprovalRequired as e:
            step("16 approval requested (upload blocked before approval)", True, reason=str(e))
        publishing.approve(eid, "demo-owner", "approved", "private", "demo approval")
        pkg = publishing.prepare_package(eid)
        files = sorted(x.name for x in pathlib.Path(pkg["package_dir"]).iterdir())
        step("17 upload package prepared (private; not uploaded - demo)", "short.mp4" in files, package=pkg["package_dir"], files=files)
        out = pathlib.Path(st["render"]["output"]["path"])
        info = media.probe(out)
        playable = media.decode_audio(str(out), t=2.0).size > 0 and media.visual_features(str(out), t=3, fps=2).get("frames", 0) >= 4
        step("valid playable MP4 (decoded video + audio)", playable and info["width"] == 1080 and info["height"] == 1920, probe=info)
    for sid in (owned["session_id"], third["session_id"]):  # stop ingest + chat cleanly (no orphan ffmpeg)
        stop_monitoring(sid)
    report["elapsed_s"] = round(time.time() - t_start, 1)
    report["passed"] = all(x["ok"] for x in report["steps"])
    report["project_id"] = pid
    if out_json:
        out_json.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report
