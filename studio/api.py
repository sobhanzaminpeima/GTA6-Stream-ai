"""FastAPI app: REST + Server-Sent Events + the dashboard (served from studio/web).

Run:  .\\.venv\\Scripts\\python -m studio serve   ->  http://127.0.0.1:8765
Binds to localhost. If STUDIO_API_TOKEN is set, every /api write needs header
X-Studio-Token (for exposing the app beyond this machine).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import pathlib
import shutil
import threading
import time
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select, update

from . import costs, discovery, events, growth, jobs, monitor, pipeline, publishing, render, rights, subtitles
from .config import ROOT, settings
from .db import audit, automation, migrate, session, set_automation
from .models import (AIScript, AnalyticsSnapshot, ApprovalRecord, AuditLog, ChatEvent, ChatWindow, ClipProject, ClipTimeline,
                     DetectedSpike, GrowthRecommendation, Job, Livestream, MediaAsset, MediaSegment, MonitoringSession,
                     MonitorStatus, Notification, PublishedVideo, RightsPermission, RightsState, ScheduledPost, Streamer,
                     SubtitleTrack, UploadJob, VideoExport, ViralCandidate, now)

WEB = pathlib.Path(__file__).parent / "web"


@contextlib.asynccontextmanager
async def lifespan(_app):
    _startup()
    yield
    _shutdown()


app = FastAPI(title="GTA VI Growth Studio - Viral Clip Hunter", version="1.0.0", lifespan=lifespan)
POOL: jobs.WorkerPool | None = None


def guard(request: Request) -> None:
    tok = settings.get("STUDIO_API_TOKEN")
    if tok and request.method not in ("GET", "HEAD") and request.headers.get("x-studio-token") != tok:
        raise HTTPException(401, "missing or wrong X-Studio-Token")


def row(o, *fields) -> dict:
    d = {c.name: getattr(o, c.name) for c in o.__table__.columns}
    if fields:
        d = {k: d[k] for k in fields}
    for k, v in d.items():
        if isinstance(v, dt.datetime):
            d[k] = v.isoformat() + "Z"
    return d


# ---------------------------------------------------------------- lifecycle
def retention_loop(stop: threading.Event) -> None:
    while not stop.wait(3600 if not stop.is_set() else 0):
        retention_pass()


def retention_pass() -> dict:
    cut_content = time.time() - settings.chat_retention_days * 86400
    cut_rows = time.time() - 30 * 86400
    with session() as s:
        a = s.execute(update(ChatEvent).where(ChatEvent.ts < cut_content, ChatEvent.content != "").values(content="", normalized="")).rowcount
        b = s.execute(delete(ChatEvent).where(ChatEvent.ts < cut_rows)).rowcount
        # buffers of finished sessions: remove unpinned segments older than the buffer window
        old = s.scalars(select(MediaSegment).join(MonitoringSession, MediaSegment.session_id == MonitoringSession.id)
                        .where(MonitoringSession.status == MonitorStatus.STOPPED.value, MediaSegment.deleted.is_(False),
                               MediaSegment.wall_start < time.time() - 3600)).all()
        n = 0
        for seg in old:
            if not seg.pinned_by:
                pathlib.Path(seg.path).unlink(missing_ok=True)
                seg.deleted = True
                n += 1
        audit(s, "retention_pass", None, chat_cleared=a, chat_deleted=b, segments_deleted=n)
    return {"chat_cleared": a, "chat_deleted": b, "segments_deleted": n}


_stop_bg = threading.Event()


def _startup() -> None:
    global POOL
    migrate()
    with session() as s:  # crash recovery: sessions that were live when the app died
        for ms in s.scalars(select(MonitoringSession).where(MonitoringSession.status.in_(
                ["MONITORING", "BUFFERING", "CONNECTED", "RECONNECTING", "PROCESSING"]))):
            ms.status, ms.status_detail, ms.ended_at = MonitorStatus.STOPPED.value, "app restarted - start monitoring again", now()
    if settings.get("STUDIO_NO_WORKERS") != "1":
        POOL = jobs.WorkerPool(2)
        POOL.start()
        threading.Thread(target=retention_loop, args=(_stop_bg,), daemon=True).start()


def _shutdown() -> None:
    _stop_bg.set()
    for sid in list(monitor.RUNTIMES):
        monitor.stop_monitoring(sid)
    if POOL:
        POOL.stop()


# ---------------------------------------------------------------- web
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/media/{path:path}")
def media_file(path: str):
    p = (settings.data_dir / path).resolve()
    if settings.data_dir.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def media_url(p: str | None) -> str | None:
    if not p:
        return None
    try:
        rel = pathlib.Path(p).resolve().relative_to(settings.data_dir.resolve())
        return "/media/" + rel.as_posix() + f"?v={int(pathlib.Path(p).stat().st_mtime)}"
    except (ValueError, FileNotFoundError):
        return None


@app.get("/api/events")
async def sse(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.to_thread(q.get, True, 15)
                    yield events.sse(msg)
                except Exception:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- overview / status
@app.get("/api/overview")
def overview():
    with session() as s:
        live = [ms for ms in s.scalars(select(MonitoringSession).where(MonitoringSession.status.notin_(["STOPPED", "IDLE"])))]
        cands = s.scalars(select(ViralCandidate).order_by(desc(ViralCandidate.overall_score)).limit(5)).all()
        in_prod = s.scalar(select(func.count()).select_from(ClipProject).where(ClipProject.status.in_(["queued", "running"])))
        ready = s.scalars(select(ClipProject).where(ClipProject.status == "ready_for_approval")).all()
        published = s.scalars(select(PublishedVideo).order_by(desc(PublishedVideo.id)).limit(5)).all()
        snaps = {sn.youtube_video_id: sn.metrics for sn in s.scalars(select(AnalyticsSnapshot))}
        msgs_min = s.scalar(select(func.coalesce(func.sum(ChatWindow.messages), 0)).where(ChatWindow.second > time.time() - 60))
        return {
            "monitored": [{"session_id": m.id, "status": m.status, "detail": m.status_detail,
                           "stream": row(s.get(Livestream, m.livestream_id), "id", "title", "channel_name", "platform", "is_simulation")}
                          for m in live],
            "chat_messages_last_minute": msgs_min,
            "candidates_total": s.scalar(select(func.count()).select_from(ViralCandidate)),
            "top_candidates": [cand_dict(s, c) for c in cands],
            "in_production": in_prod, "ready_for_approval": [{"id": p.id, "title": p.title} for p in ready],
            "latest_published": [{"id": v.youtube_video_id, "title": v.title, "metrics": snaps.get(v.youtube_video_id)} for v in published],
            "channel": {"name": settings.channel_name, "handle": settings.channel_handle},
            "costs": costs.summary(s),
        }


@app.get("/api/status")
def status():
    with session() as s:
        sessions = s.scalars(select(MonitoringSession).order_by(desc(MonitoringSession.id)).limit(20)).all()
        failed = s.scalars(select(Job).where(Job.status == "failed").order_by(desc(Job.id)).limit(10)).all()
        out_sessions = [{**row(m), "stream": s.get(Livestream, m.livestream_id).title,
                         "alive": bool(monitor.RUNTIMES.get(m.id) and monitor.RUNTIMES[m.id].thread.is_alive())} for m in sessions]
    usage = shutil.disk_usage(settings.data_dir)
    size = sum(f.stat().st_size for f in settings.data_dir.rglob("*") if f.is_file())
    return {"connections": publishing.connection_status(), "sessions": out_sessions,
            "failed_jobs": [{"id": j.id, "kind": j.kind, "error": (j.error or "")[:400]} for j in failed],
            "storage": {"studio_bytes": size, "disk_free_bytes": usage.free, "disk_total_bytes": usage.total,
                        "warning": usage.free < 5 * 1024 ** 3},
            "workers": bool(POOL and any(t.is_alive() for t in POOL.threads))}


# ---------------------------------------------------------------- streams / discovery
@app.get("/api/streams")
def list_streams(live: bool | None = None, platform: str | None = None, language: str | None = None,
                 min_viewers: int | None = None, max_viewers: int | None = None, content_type: str | None = None,
                 authorization: str | None = None, monitoring: str | None = None):
    with session() as s:
        q = select(Livestream).order_by(desc(Livestream.viewer_count), desc(Livestream.id))
        if live is not None:
            q = q.where(Livestream.is_live.is_(live))
        if platform:
            q = q.where(Livestream.platform == platform)
        if language:
            q = q.where(Livestream.language.like(f"{language}%"))
        if min_viewers is not None:
            q = q.where(Livestream.viewer_count >= min_viewers)
        if max_viewers is not None:
            q = q.where(Livestream.viewer_count <= max_viewers)
        if content_type:
            q = q.where(Livestream.content_type == content_type)
        out = []
        for ls in s.scalars(q.limit(300)):
            d = row(ls)
            dec = rights.decide(s, ls)
            ms = s.scalar(select(MonitoringSession).where(MonitoringSession.livestream_id == ls.id).order_by(desc(MonitoringSession.id)))
            d["authorization"] = dec.state
            d["can_record"] = dec.can_record
            d["monitoring_status"] = ms.status if ms else "IDLE"
            d["session_id"] = ms.id if ms else None
            d["candidate_count"] = s.scalar(select(func.count()).select_from(ViralCandidate).where(ViralCandidate.livestream_id == ls.id))
            d["chat_last_minute"] = s.scalar(select(func.coalesce(func.sum(ChatWindow.messages), 0)).where(
                ChatWindow.session_id == ms.id, ChatWindow.second > time.time() - 60)) if ms else 0
            if authorization and d["authorization"] != authorization:
                continue
            if monitoring and d["monitoring_status"] != monitoring:
                continue
            out.append(d)
        return out


class DiscoveryIn(BaseModel):
    keywords: list[str] | None = None
    language: str | None = "en"
    platforms: list[str] = ["youtube", "twitch"]


@app.post("/api/discovery/run", dependencies=[Depends(guard)])
def discovery_run(body: DiscoveryIn):
    return discovery.run_discovery(body.keywords, body.language, tuple(body.platforms))


class StreamIn(BaseModel):
    platform: str
    stream_id: str
    title: str = ""
    channel_name: str = ""
    channel_id: str | None = None
    source_url: str = ""
    language: str | None = "en"
    media_source: str | None = None


@app.post("/api/streams", dependencies=[Depends(guard)])
def add_stream(body: StreamIn):
    with session() as s:
        ls = discovery.upsert(s, body.model_dump())
        if body.media_source:
            ls.media_source = body.media_source
        s.flush()
        return row(ls)


class StreamPatch(BaseModel):
    content_type: str | None = None
    media_source: str | None = None


@app.patch("/api/streams/{sid}", dependencies=[Depends(guard)])
def patch_stream(sid: int, body: StreamPatch):
    with session() as s:
        ls = s.get(Livestream, sid)
        if not ls:
            raise HTTPException(404)
        if body.content_type:
            ls.content_type, ls.classification_reason = body.content_type, "owner verified"
        if body.media_source is not None:
            ls.media_source = body.media_source or None
            audit(s, "media_source_set", f"livestream:{sid}", "owner", "security", source=body.media_source)
        return row(ls)


# ---------------------------------------------------------------- streamers / watchlist
class StreamerIn(BaseModel):
    platform: str
    ref: str
    name: str | None = None
    language: str = "en"
    priority: int = 3
    content_category: str = "unverified"


@app.get("/api/streamers")
def list_streamers():
    with session() as s:
        out = []
        for st in s.scalars(select(Streamer).order_by(Streamer.priority, Streamer.name)):
            d = row(st)
            ls = s.scalars(select(Livestream.id).where(Livestream.streamer_id == st.id)).all()
            d["candidates"] = s.scalar(select(func.count()).select_from(ViralCandidate).where(ViralCandidate.livestream_id.in_(ls))) if ls else 0
            d["rights"] = [row(r) for r in st.rights]
            out.append(d)
        return out


@app.post("/api/streamers", dependencies=[Depends(guard)])
def add_streamer(body: StreamerIn):
    sid = discovery.add_streamer(body.platform, body.ref, body.name, body.language, body.priority, body.content_category)
    with session() as s:
        return row(s.get(Streamer, sid))


class StreamerPatch(BaseModel):
    name: str | None = None
    priority: int | None = None
    language: str | None = None
    content_category: str | None = None
    active_monitoring: bool | None = None
    schedule: dict | None = None
    custom_keywords: list[str] | None = None
    thresholds: dict | None = None
    notify: bool | None = None


@app.patch("/api/streamers/{sid}", dependencies=[Depends(guard)])
def patch_streamer(sid: int, body: StreamerPatch):
    with session() as s:
        st = s.get(Streamer, sid)
        if not st:
            raise HTTPException(404)
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(st, k, v)
        return row(st)


@app.delete("/api/streamers/{sid}", dependencies=[Depends(guard)])
def del_streamer(sid: int):
    with session() as s:
        st = s.get(Streamer, sid)
        if st:
            for r in list(st.rights):
                s.delete(r)
            s.execute(update(Livestream).where(Livestream.streamer_id == sid).values(streamer_id=None))
            s.delete(st)
            audit(s, "streamer_removed", f"streamer:{sid}", "owner")
    return {"ok": True}


# ---------------------------------------------------------------- rights
class RightsIn(BaseModel):
    streamer_id: int | None = None
    source_url: str | None = None
    state: RightsState
    content_owner: str | None = None
    evidence: str | None = None
    commercial_use: bool = False
    editing_allowed: bool = False
    recording_allowed: bool = False
    attribution_required: str | None = None
    expires_at: dt.datetime | None = None
    platforms_allowed: list[str] = ["youtube"]
    notes: str | None = None


@app.get("/api/rights")
def list_rights():
    with session() as s:
        return [row(r) for r in s.scalars(select(RightsPermission).order_by(desc(RightsPermission.id)))]


@app.post("/api/rights", dependencies=[Depends(guard)])
def add_rights(body: RightsIn):
    if not body.streamer_id and not body.source_url:
        raise HTTPException(400, "give a streamer_id or a source_url")
    if body.state in (RightsState.EXPLICITLY_LICENSED, RightsState.AUTHORIZED_FOR_CLIPPING) and not (body.evidence or "").strip():
        raise HTTPException(400, "licensed/authorized states need evidence (email, contract or campaign terms link)")
    with session() as s:
        d = body.model_dump()
        d["state"] = body.state.value
        if d["expires_at"]:
            d["expires_at"] = d["expires_at"].replace(tzinfo=None)
        r = RightsPermission(**d)
        s.add(r)
        s.flush()
        audit(s, "rights_recorded", f"rights:{r.id}", "owner", "security", state=r.state, evidence=r.evidence)
        return row(r)


@app.delete("/api/rights/{rid}", dependencies=[Depends(guard)])
def del_rights(rid: int):
    with session() as s:
        r = s.get(RightsPermission, rid)
        if r:
            s.delete(r)
            audit(s, "rights_removed", f"rights:{rid}", "owner", "security")
    return {"ok": True}


# ---------------------------------------------------------------- monitoring
class MonitorIn(BaseModel):
    livestream_id: int
    record_media: bool = False


@app.post("/api/monitor/start", dependencies=[Depends(guard)])
def monitor_start(body: MonitorIn):
    try:
        sid = monitor.start_monitoring(body.livestream_id, body.record_media)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with session() as s:
        return row(s.get(MonitoringSession, sid))


@app.post("/api/monitor/{sid}/stop", dependencies=[Depends(guard)])
def monitor_stop(sid: int):
    monitor.stop_monitoring(sid)
    return {"ok": True}


@app.get("/api/monitor/sessions")
def sessions():
    with session() as s:
        return [{**row(m), "stream": row(s.get(Livestream, m.livestream_id), "id", "title", "channel_name", "platform", "is_simulation")}
                for m in s.scalars(select(MonitoringSession).order_by(desc(MonitoringSession.id)).limit(50))]


@app.get("/api/monitor/{sid}/timeline")
def timeline(sid: int, since: int = 0, last: int = 900):
    with session() as s:
        ms = s.get(MonitoringSession, sid)
        if not ms:
            raise HTTPException(404)
        # live: the last N seconds; finished session: the last N seconds it actually had
        newest = s.scalar(select(func.max(ChatWindow.second)).where(ChatWindow.session_id == sid)) or int(time.time())
        lo = since or (min(int(time.time()), newest) - last)
        win = s.scalars(select(ChatWindow).where(ChatWindow.session_id == sid, ChatWindow.second >= lo).order_by(ChatWindow.second)).all()
        spikes = s.scalars(select(DetectedSpike).where(DetectedSpike.session_id == sid, DetectedSpike.ts >= lo)).all()
        segs = s.scalars(select(MediaSegment).where(MediaSegment.session_id == sid, MediaSegment.wall_start >= lo)).all()
        cands = s.scalars(select(ViralCandidate).where(ViralCandidate.session_id == sid)).all()
        recent = s.scalars(select(ChatEvent).where(ChatEvent.session_id == sid).order_by(desc(ChatEvent.ts)).limit(30)).all()
        return {"session": row(ms), "points": [{"t": w.second, "messages": w.messages, "users": w.unique_users, "reaction": w.reaction,
                                                "score": w.score, "spam": w.spam} for w in win],
                "spikes": [{"t": sp.ts, "score": sp.anomaly_score, "suppressed": sp.suppressed, "reason": sp.suppress_reason,
                            "explanation": sp.explanation, "candidate_id": sp.candidate_id} for sp in spikes],
                "segments": [{"t": x.wall_start, "d": x.duration, "audio": x.audio_score, "visual": x.visual_score,
                              "deleted": x.deleted} for x in segs],
                "candidates": [{"id": c.id, "t": c.event_ts, "score": c.overall_score, "tier": c.tier} for c in cands],
                "recent_chat": [{"t": e.ts, "text": e.content, "kw": e.keywords, "spam": e.is_spam, "kind": e.kind} for e in reversed(recent)]}


class SimIn(BaseModel):
    record_media: bool = True
    source: str | None = None
    restricted: bool = False


@app.post("/api/simulate", dependencies=[Depends(guard)])
def simulate(body: SimIn):
    from .demo import start_simulation
    return start_simulation(record_media=body.record_media, source=body.source, restricted=body.restricted)


# ---------------------------------------------------------------- candidates
def cand_dict(s, c: ViralCandidate) -> dict:
    d = row(c)
    ls = s.get(Livestream, c.livestream_id)
    d["stream"] = {"id": ls.id, "title": ls.title, "channel_name": ls.channel_name, "platform": ls.platform,
                   "content_type": ls.content_type, "is_simulation": ls.is_simulation}
    asset = s.scalar(select(MediaAsset).where(MediaAsset.candidate_id == c.id, MediaAsset.kind == "candidate_clip"))
    d["clip_url"] = media_url(asset.path) if asset else None
    d["projects"] = [{"id": p.id, "status": p.status, "stage": p.stage} for p in
                     s.scalars(select(ClipProject).where(ClipProject.candidate_id == c.id))]
    return d


@app.get("/api/candidates")
def list_candidates(status: str | None = None, min_score: float | None = None, rights_state: str | None = None,
                    platform: str | None = None, sort: str = "score", session_id: int | None = None):
    with session() as s:
        q = select(ViralCandidate)
        if status:
            q = q.where(ViralCandidate.production_status == status)
        else:
            q = q.where(ViralCandidate.production_status != "dismissed")
        if min_score is not None:
            q = q.where(ViralCandidate.overall_score >= min_score)
        if rights_state:
            q = q.where(ViralCandidate.rights_state == rights_state)
        if session_id:
            q = q.where(ViralCandidate.session_id == session_id)
        q = q.order_by(desc(ViralCandidate.overall_score) if sort == "score" else desc(ViralCandidate.created_at))
        out = [cand_dict(s, c) for c in s.scalars(q.limit(300))]
        if platform:
            out = [c for c in out if c["stream"]["platform"] == platform]
        return out


@app.get("/api/candidates/{cid}")
def get_candidate(cid: int):
    with session() as s:
        c = s.get(ViralCandidate, cid)
        if not c:
            raise HTTPException(404)
        d = cand_dict(s, c)
        d["spikes"] = [row(x) for x in s.scalars(select(DetectedSpike).where(DetectedSpike.candidate_id == cid))]
        d["chat_sample"] = [{"t": e.ts, "text": e.content, "kw": e.keywords} for e in s.scalars(
            select(ChatEvent).where(ChatEvent.session_id == c.session_id, ChatEvent.reaction_weight > 0,
                                    ChatEvent.ts.between(c.first_spike_ts - 12, c.last_spike_ts + 4)).limit(40))]
        return d


class CandPatch(BaseModel):
    notes: str | None = None
    production_status: str | None = Field(None, pattern="^(new|saved|dismissed)$")


@app.patch("/api/candidates/{cid}", dependencies=[Depends(guard)])
def patch_candidate(cid: int, body: CandPatch):
    with session() as s:
        c = s.get(ViralCandidate, cid)
        if not c:
            raise HTTPException(404)
        if body.notes is not None:
            c.notes = body.notes
        if body.production_status:
            c.production_status = body.production_status
            audit(s, f"candidate_{body.production_status}", f"candidate:{cid}", "owner")
        return cand_dict(s, c)


class CalIn(BaseModel):
    true_media_ts: float


@app.post("/api/candidates/{cid}/calibrate", dependencies=[Depends(guard)])
def calibrate(cid: int, body: CalIn):
    try:
        return {"chat_latency_s": monitor.calibrate(cid, body.true_media_ts)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/candidates/{cid}/report")
def candidate_report(cid: int):
    """Discovery report for a moment we may not capture: link + timestamp + evidence + permission request."""
    with session() as s:
        c = s.get(ViralCandidate, cid)
        ls = s.get(Livestream, c.livestream_id)
        d = rights.decide(s, ls)
        when = dt.datetime.fromtimestamp(c.event_ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        req = (f"Hi {ls.channel_name}, I run GTA VI Central (YouTube {settings.channel_handle}). Your stream '{ls.title}' had a "
               f"moment around {when} that your chat loved. May I use about 30 seconds of it in a Short with my own "
               f"commentary, credit and a link to your channel? I'd need permission to record/edit it and to publish on "
               f"YouTube (monetized). Thanks!")
        return {"candidate_id": cid, "stream": ls.title, "channel": ls.channel_name, "source_url": ls.source_url,
                "timestamp_url": c.source_timestamp_url, "event_time_utc": when, "score": c.overall_score, "tier": c.tier,
                "confidence": c.confidence, "rights": d.as_dict(), "evidence": c.explanation,
                "media_acquired": c.clip_readiness not in ("report_only", "no_media"), "permission_request_draft": req}


class GenIn(BaseModel):
    preset: str = "CINEMATIC"
    cut: str = "B"


@app.post("/api/candidates/{cid}/generate", dependencies=[Depends(guard)])
def generate(cid: int, body: GenIn):
    with session() as s:
        c = s.get(ViralCandidate, cid)
        if not c:
            raise HTTPException(404)
        ls = s.get(Livestream, c.livestream_id)
        d = rights.decide(s, ls)
        if not d.can_edit:
            raise HTTPException(403, f"rights block production: {'; '.join(d.reasons)}. Request footage permission first.")
        if c.clip_readiness != "clip_ready":
            raise HTTPException(409, f"clip not ready yet ({c.clip_readiness})")
    if body.preset not in render.presets():
        raise HTTPException(400, "unknown preset")
    pid = pipeline.create_project(cid, body.preset, body.cut)
    jid = jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=2)
    return {"project_id": pid, "job_id": jid}


@app.post("/api/uploads/source", dependencies=[Depends(guard)])
async def upload_source(file: UploadFile = File(...), title: str = Form(...), owner: str = Form(...),
                        state: RightsState = Form(RightsState.OWNED), evidence: str = Form(""),
                        moment_at: float | None = Form(None), content_type: str = Form("unverified")):
    """Your own recording or a licensed clip. Creates a stream + rights record, finds the moment
    (given timestamp, or the loudest/most active point) and extracts the candidate clip."""
    if state not in (RightsState.OWNED, RightsState.EXPLICITLY_LICENSED, RightsState.AUTHORIZED_FOR_CLIPPING):
        raise HTTPException(400, "uploads must be owned or licensed footage")
    if state != RightsState.OWNED and not evidence.strip():
        raise HTTPException(400, "licensed uploads need evidence")
    folder = settings.data_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{int(time.time())}_{pathlib.Path(file.filename or 'clip.mp4').name}"
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    from .demo import register_upload
    return register_upload(dest, title, owner, state.value, evidence, moment_at, content_type)


# ---------------------------------------------------------------- projects / studio
def project_dict(s, p: ClipProject) -> dict:
    d = row(p)
    folder = pathlib.Path(p.folder)
    d["files"] = {k: media_url(str(folder / v)) for k, v in {"short": "short.mp4", "cover": "cover.jpg", "thumbnail": "thumbnail.jpg",
                                                             "contact_sheet": "contact_sheet.png", "clip": "clip.mp4"}.items()
                  if (folder / v).exists()}
    ex = s.scalar(select(VideoExport).where(VideoExport.project_id == p.id).order_by(desc(VideoExport.id)))
    d["export"] = row(ex) if ex else None
    if ex:
        ap = publishing.latest_approval(s, ex.id)
        d["approval"] = row(ap) if ap else None
        up = s.scalar(select(UploadJob).where(UploadJob.export_id == ex.id))
        d["upload"] = row(up) if up else None
    sub = s.scalar(select(SubtitleTrack).where(SubtitleTrack.project_id == p.id))
    d["srt"] = pathlib.Path(sub.srt_path).read_text(encoding="utf-8") if sub and pathlib.Path(sub.srt_path).exists() else ""
    tl = s.scalar(select(ClipTimeline).where(ClipTimeline.project_id == p.id).order_by(desc(ClipTimeline.id)))
    d["timeline"] = row(tl) if tl else None
    job = s.scalar(select(Job).where(Job.kind == "generate_short").order_by(desc(Job.id)).filter(Job.payload["project_id"].as_integer() == p.id))
    d["job"] = row(job, "id", "status", "progress", "message", "error", "attempts") if job else None
    c = s.get(ViralCandidate, p.candidate_id)
    d["candidate"] = {"id": c.id, "score": c.overall_score, "rights_state": c.rights_state}
    return d


@app.get("/api/projects")
def list_projects():
    with session() as s:
        return [project_dict(s, p) for p in s.scalars(select(ClipProject).order_by(desc(ClipProject.id)).limit(100))]


@app.get("/api/projects/{pid}")
def get_project(pid: int):
    with session() as s:
        p = s.get(ClipProject, pid)
        if not p:
            raise HTTPException(404)
        return project_dict(s, p)


class RegenIn(BaseModel):
    stage: str


@app.post("/api/projects/{pid}/regenerate", dependencies=[Depends(guard)])
def regen(pid: int, body: RegenIn):
    try:
        pipeline.regenerate(pid, body.stage)
    except ValueError:
        raise HTTPException(400, f"stage must be one of {pipeline.STAGES}")
    return {"job_id": jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=2)}


class ScriptIn(BaseModel):
    intro_lines: list[str]
    outro_lines: list[str]


@app.put("/api/projects/{pid}/script", dependencies=[Depends(guard)])
def edit_script(pid: int, body: ScriptIn):
    """Manual edit -> re-voice only changed sentences, then re-cut captions and re-render."""
    with session() as s:
        p = s.get(ClipProject, pid)
        st = dict(p.stages)
        sc = st["commentary"]["output"]["script"]
        sc["intro_lines"], sc["outro_lines"] = body.intro_lines, body.outro_lines
        st["commentary"]["output"]["script"] = sc
        st["commentary"]["output"]["edited_by_owner"] = True
        p.stages = st
        s.execute(update(SubtitleTrack).where(SubtitleTrack.project_id == pid).values(edited=False))
    pipeline.regenerate(pid, "narration")
    return {"job_id": jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=2)}


class CaptionsIn(BaseModel):
    srt: str


@app.put("/api/projects/{pid}/captions", dependencies=[Depends(guard)])
def edit_captions(pid: int, body: CaptionsIn):
    cues = subtitles.parse_srt(body.srt)
    if not cues:
        raise HTTPException(400, "could not parse SRT")
    with session() as s:
        sub = s.scalar(select(SubtitleTrack).where(SubtitleTrack.project_id == pid))
        sub.cues, sub.edited = cues, True
    pipeline.regenerate(pid, "captions")
    return {"job_id": jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=2)}


class TimelineIn(BaseModel):
    selected_cut: str | None = None
    preset: str | None = None
    cut_in: float | None = None
    cut_out: float | None = None


@app.put("/api/projects/{pid}/timeline", dependencies=[Depends(guard)])
def edit_timeline(pid: int, body: TimelineIn):
    with session() as s:
        p = s.get(ClipProject, pid)
        tl = s.scalar(select(ClipTimeline).where(ClipTimeline.project_id == pid).order_by(desc(ClipTimeline.id)))
        restart = "graphics"
        if body.preset:
            if body.preset not in render.presets():
                raise HTTPException(400, "unknown preset")
            p.preset = body.preset
            restart = "captions"
        if body.selected_cut or body.cut_in is not None:
            src = p.stages["retrieve_footage"]["output"]
            cuts = list(tl.cuts or src["cuts"])
            if body.cut_in is not None and body.cut_out is not None:  # manual trim (media time)
                lo, hi = src["media_in"], src["media_out"]
                i, o = max(lo, body.cut_in), min(hi, body.cut_out)
                if o - i < 8:
                    raise HTTPException(400, "manual cut must be at least 8 s inside the extracted footage")
                ev = s.get(ViralCandidate, p.candidate_id).media_ts
                cuts = [x for x in cuts if x["label"] != "M"] + [{"label": "M", "in": i, "out": o, "duration": round(o - i, 2),
                                                                   "event_at": round(min(max(ev - i, 1), o - i - 1), 2)}]
                body.selected_cut = "M"
            s.add(ClipTimeline(project_id=pid, version=tl.version + 1, cuts=cuts, selected_cut=body.selected_cut or tl.selected_cut))
            restart = "select_cuts"
    pipeline.regenerate(pid, restart)
    return {"job_id": jobs.enqueue("generate_short", {"project_id": pid}, max_attempts=2)}


# ---------------------------------------------------------------- approval / publishing
class ApproveIn(BaseModel):
    decision: str = "approved"
    visibility: str = "private"
    note: str | None = None
    approver: str = "owner"
    checklist: dict = {}


@app.post("/api/exports/{eid}/approve", dependencies=[Depends(guard)])
def approve(eid: int, body: ApproveIn):
    try:
        rid = publishing.approve(eid, body.approver, body.decision, body.visibility, body.note, body.checklist)
    except publishing.ApprovalRequired as e:
        raise HTTPException(409, str(e))
    return {"approval_id": rid}


class PrepIn(BaseModel):
    privacy: str = "private"
    publish_at: dt.datetime | None = None
    playlist_id: str | None = None


@app.post("/api/exports/{eid}/prepare-upload", dependencies=[Depends(guard)])
def prepare(eid: int, body: PrepIn):
    try:
        return publishing.prepare_package(eid, body.privacy, body.publish_at.replace(tzinfo=None) if body.publish_at else None, body.playlist_id)
    except publishing.ApprovalRequired as e:
        raise HTTPException(409, str(e))


@app.post("/api/uploads/{uid}/upload", dependencies=[Depends(guard)])
def do_upload(uid: int):
    try:
        return publishing.upload(uid)
    except (publishing.ApprovalRequired, PermissionError) as e:
        raise HTTPException(409, str(e))
    except costs.BudgetExceeded as e:
        raise HTTPException(429, str(e))


@app.get("/oauth/youtube/start")
def oauth_start(request: Request):
    try:
        return RedirectResponse(publishing.auth_url(str(request.url_for("oauth_callback"))))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/oauth/callback", name="oauth_callback")
def oauth_callback(request: Request, code: str, state: str):
    ch = publishing.finish_oauth(code, state, str(request.url_for("oauth_callback")))
    return RedirectResponse(f"/#settings?connected={ch.get('handle') or ch.get('title')}")


# ---------------------------------------------------------------- analytics / growth / costs / calendar
@app.get("/api/analytics")
def analytics():
    with session() as s:
        vids = s.scalars(select(PublishedVideo).order_by(desc(PublishedVideo.id))).all()
        snaps: dict[str, Any] = {}
        for sn in s.scalars(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.day)):
            snaps[sn.youtube_video_id] = {"day": sn.day, **sn.metrics}
        return {"connected": publishing.youtube_oauth_token() is not None,
                "videos": [{**row(v), "metrics": snaps.get(v.youtube_video_id)} for v in vids],
                "note": "Only metrics returned by the YouTube Analytics API are shown; blanks mean 'not available'."}


@app.post("/api/analytics/refresh", dependencies=[Depends(guard)])
def analytics_refresh():
    with session() as s:
        ids = [v.youtube_video_id for v in s.scalars(select(PublishedVideo))]
    if not ids:
        return {"status": "NO_VIDEOS"}
    return publishing.fetch_analytics(ids)


@app.get("/api/growth")
def growth_latest():
    with session() as s:
        g = s.scalar(select(GrowthRecommendation).order_by(desc(GrowthRecommendation.id)))
        return g.report if g else None


@app.post("/api/growth/run", dependencies=[Depends(guard)])
def growth_run():
    return growth.weekly_report()


@app.get("/api/costs")
def costs_summary():
    with session() as s:
        return costs.summary(s)


@app.get("/api/calendar")
def calendar():
    with session() as s:
        return [row(x) for x in s.scalars(select(ScheduledPost).order_by(ScheduledPost.planned_for))]


class CalendarIn(BaseModel):
    title: str
    planned_for: dt.datetime
    project_id: int | None = None
    notes: str | None = None


@app.post("/api/calendar", dependencies=[Depends(guard)])
def calendar_add(body: CalendarIn):
    with session() as s:
        x = ScheduledPost(title=body.title, planned_for=body.planned_for.replace(tzinfo=None), project_id=body.project_id, notes=body.notes)
        s.add(x)
        s.flush()
        return row(x)


@app.delete("/api/calendar/{cid}", dependencies=[Depends(guard)])
def calendar_del(cid: int):
    with session() as s:
        x = s.get(ScheduledPost, cid)
        if x:
            s.delete(x)
    return {"ok": True}


# ---------------------------------------------------------------- API keys (stored locally, never echoed back)
@app.get("/api/keys")
def get_keys():
    from .config import ENV_FILE, ENV_KEYS
    return {"keys": {k: bool(settings.get(k)) for k in ENV_KEYS}, "env_file": str(ENV_FILE),
            "oauth_client": publishing.client_config() is not None,
            "oauth_client_file": str(settings.youtube_client_secrets),
            "values": {k: settings.get(k) for k in ("STUDIO_CHANNEL_HANDLE", "STUDIO_CHANNEL_NAME")}}


@app.put("/api/keys", dependencies=[Depends(guard)])
def put_keys(values: dict[str, str]):
    try:
        settings.save_env({k: v for k, v in values.items() if v is not None})
    except ValueError as e:
        raise HTTPException(400, str(e))
    with session() as s:
        audit(s, "keys_updated", None, "owner", "security", keys=sorted(values))  # names only, never values
    return get_keys()


@app.post("/api/keys/oauth-client", dependencies=[Depends(guard)])
async def put_oauth_client(file: UploadFile = File(...)):
    import json as _json
    raw = await file.read()
    try:
        d = _json.loads(raw)
        c = d.get("installed") or d.get("web")
        assert c and c.get("client_id") and c.get("client_secret")
    except Exception:
        raise HTTPException(400, "not a Google OAuth client JSON (download it from Cloud Console > Clients)")
    if "installed" not in d:
        raise HTTPException(400, "use an OAuth client of type 'Desktop app'")
    dest = settings.youtube_client_secrets
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    with session() as s:
        audit(s, "oauth_client_saved", None, "owner", "security")
    return get_keys()


# ---------------------------------------------------------------- settings / misc
@app.get("/api/automation")
def get_automation():
    with session() as s:
        return automation(s)


@app.put("/api/automation", dependencies=[Depends(guard)])
def put_automation(patch: dict):
    with session() as s:
        try:
            out = set_automation(s, patch)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(s, "automation_changed", None, "owner", "info", keys=list(patch))
        return out


@app.get("/api/presets")
def get_presets():
    return render.presets()


@app.put("/api/presets/{name}", dependencies=[Depends(guard)])
def put_preset(name: str, values: dict):
    return render.save_preset(name, values)


@app.get("/api/notifications")
def notifications():
    with session() as s:
        return [row(n) for n in s.scalars(select(Notification).order_by(desc(Notification.id)).limit(50))]


@app.post("/api/notifications/read", dependencies=[Depends(guard)])
def notifications_read():
    with session() as s:
        s.execute(update(Notification).values(read=True))
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs():
    with session() as s:
        return [row(j, "id", "kind", "status", "progress", "message", "error", "attempts", "created_at")
                for j in s.scalars(select(Job).order_by(desc(Job.id)).limit(50))]


@app.post("/api/jobs/{jid}/cancel", dependencies=[Depends(guard)])
def cancel_job(jid: int):
    jobs.cancel(jid)
    return {"ok": True}


@app.get("/api/audit")
def audit_log(limit: int = 100):
    with session() as s:
        return [row(a) for a in s.scalars(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit))]


@app.post("/api/maintenance/retention", dependencies=[Depends(guard)])
def retention_now():
    return retention_pass()
