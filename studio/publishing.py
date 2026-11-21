"""YouTube publishing + analytics through the official APIs, behind the approval gate.

OAuth 2.0 (installed-app / loopback redirect to this app). Tokens are stored encrypted
(Fernet). Uploads default to PRIVATE; public needs an approval that says public.
Idempotency: one upload per (file hash, channel) - a repeat returns the existing id.
Resumable uploads survive interruptions (we store the session URI and resume).
Never generates engagement of any kind.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import secrets
import shutil
import urllib.parse

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select

from . import costs
from .config import settings
from .db import audit, session
from .models import (AnalyticsSnapshot, ApprovalRecord, ClipProject, ConnectedPlatform, PublishedVideo, UploadJob,
                     VideoExport, now)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
_states: dict[str, float] = {}


class ApprovalRequired(PermissionError):
    pass


def _fernet() -> Fernet:
    return Fernet(settings.secret_key)


def encrypt(d: dict) -> str:
    return _fernet().encrypt(json.dumps(d).encode()).decode()


def decrypt(s: str) -> dict:
    return json.loads(_fernet().decrypt(s.encode()))


def client_config() -> dict | None:
    p = settings.youtube_client_secrets
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("installed") or d.get("web")


def auth_url(redirect_uri: str) -> str:
    cfg = client_config()
    if not cfg:
        raise RuntimeError(f"OAuth client not configured: download an OAuth client (Desktop app) JSON from Google Cloud "
                           f"Console and save it as {settings.youtube_client_secrets}")
    state = secrets.token_urlsafe(24)
    _states[state] = now().timestamp()
    return AUTH + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true"})


def finish_oauth(code: str, state: str, redirect_uri: str) -> dict:
    if state not in _states:
        raise PermissionError("OAuth state mismatch")
    _states.pop(state)
    cfg = client_config()
    r = httpx.post(TOKEN, data={"code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=30)
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = now().timestamp() + tok.get("expires_in", 3600)
    ch = _channel(tok["access_token"])
    with session() as s:
        row = s.scalar(select(ConnectedPlatform).where(ConnectedPlatform.platform == "youtube")) or ConnectedPlatform(platform="youtube")
        row.token_encrypted, row.scopes, row.last_checked = encrypt(tok), SCOPES, now()
        row.account_name, row.account_id = ch.get("handle") or ch.get("title"), ch.get("id")
        expected = settings.channel_handle.lower()
        row.status = "CONNECTED" if (ch.get("handle") or "").lower() == expected else "CONNECTED_OTHER_CHANNEL"
        row.detail = f"connected channel {ch.get('title')} ({ch.get('handle')}); expected {settings.channel_handle}"
        s.add(row)
        audit(s, "youtube_connected", ch.get("id"), "owner", "security", handle=ch.get("handle"))
    return ch


def _channel(access_token: str) -> dict:
    r = httpx.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                  headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    r.raise_for_status()
    it = (r.json().get("items") or [{}])[0]
    sn = it.get("snippet", {})
    return {"id": it.get("id"), "title": sn.get("title"), "handle": sn.get("customUrl")}


def youtube_oauth_token() -> str | None:
    """Fresh access token or None when the channel isn't connected."""
    with session() as s:
        row = s.scalar(select(ConnectedPlatform).where(ConnectedPlatform.platform == "youtube"))
        if not row or not row.token_encrypted:
            return None
        tok = decrypt(row.token_encrypted)
        if tok.get("expires_at", 0) > now().timestamp() + 60:
            return tok["access_token"]
        cfg = client_config()
        if not cfg or not tok.get("refresh_token"):
            row.status = "EXPIRED"
            return None
        r = httpx.post(TOKEN, data={"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
        if r.status_code >= 400:
            row.status, row.detail = "REAUTH_REQUIRED", r.text[:300]
            return None
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=now().timestamp() + new.get("expires_in", 3600))
        row.token_encrypted, row.last_checked = encrypt(tok), now()
        return tok["access_token"]


# ---------------------------------------------------------------- approval + package
def approve(export_id: int, approver: str, decision: str, visibility: str = "private", note: str | None = None,
            checklist: dict | None = None) -> int:
    if decision not in ("approved", "rejected", "changes_requested"):
        raise ValueError(decision)
    if visibility not in ("private", "unlisted", "public"):
        raise ValueError(visibility)
    with session() as s:
        ex = s.get(VideoExport, export_id)
        p = s.get(ClipProject, ex.project_id)
        if decision == "approved" and not (p.qc or {}).get("passed"):
            raise ApprovalRequired("QC has blocking failures; fix them before approving")
        r = ApprovalRecord(export_id=export_id, decision=decision, visibility_approved=visibility, approver=approver,
                           note=note, checklist=checklist or {})
        s.add(r)
        p.status = {"approved": "approved", "rejected": "rejected", "changes_requested": "changes_requested"}[decision]
        from .models import ViralCandidate
        c = s.get(ViralCandidate, p.candidate_id)
        c.production_status = "approved" if decision == "approved" else "in_production"
        audit(s, f"export_{decision}", f"export:{export_id}", approver, "security", visibility=visibility)
        s.flush()
        return r.id


def latest_approval(s, export_id: int) -> ApprovalRecord | None:
    return s.scalar(select(ApprovalRecord).where(ApprovalRecord.export_id == export_id).order_by(ApprovalRecord.id.desc()))


def prepare_package(export_id: int, privacy: str = "private", publish_at: dt.datetime | None = None,
                    playlist_id: str | None = None) -> dict:
    """Upload package on disk (works without credentials). Requires an approval."""
    with session() as s:
        ex = s.get(VideoExport, export_id)
        ap = latest_approval(s, export_id)
        if not ap or ap.decision != "approved":
            raise ApprovalRequired("the owner has not approved this export")
        order = ["private", "unlisted", "public"]
        if order.index(privacy) > order.index(ap.visibility_approved):
            raise ApprovalRequired(f"approved visibility is '{ap.visibility_approved}', not '{privacy}'")
        channel = settings.channel_handle
        key = f"{ex.sha256[:40]}:{channel}"
        job = s.scalar(select(UploadJob).where(UploadJob.idempotency_key == key))
        pkg = settings.data_dir / "upload_packages" / f"project_{ex.project_id}"
        pkg.mkdir(parents=True, exist_ok=True)
        folder = pathlib.Path(ex.path).parent
        for src in (pathlib.Path(ex.path), pathlib.Path(ex.cover_path or ""), pathlib.Path(ex.thumb_path or ""),
                    folder / "captions.srt", folder / "description.txt", folder / "metadata.json", folder / "contact_sheet.png"):
            if src.is_file():
                shutil.copy2(src, pkg / src.name)
        md = dict(ex.metadata_json)
        (pkg / "upload.json").write_text(json.dumps({
            "title": md["recommended_title"], "description": md["description"], "tags": md["tags"],
            "categoryId": md["category_id"], "privacyStatus": privacy, "publishAt": publish_at.isoformat() if publish_at else None,
            "madeForKids": False, "containsSyntheticMedia": True, "playlist": md["playlist_suggestion"],
            "pinned_comment": md["pinned_comment"]}, indent=1), encoding="utf-8")
        if job is None:
            job = UploadJob(export_id=export_id, idempotency_key=key, privacy=privacy, publish_at=publish_at,
                            playlist_id=playlist_id, package_dir=str(pkg))
            s.add(job)
        elif job.status != "uploaded":
            job.privacy, job.publish_at, job.playlist_id, job.package_dir = privacy, publish_at, playlist_id, str(pkg)
        s.flush()
        audit(s, "upload_prepared", f"export:{export_id}", "owner", package=str(pkg), privacy=privacy)
        return {"upload_job_id": job.id, "package_dir": str(pkg), "status": job.status, "youtube_video_id": job.youtube_video_id}


def upload(upload_job_id: int, http: httpx.Client | None = None, chunk: int = 8 * 1024 * 1024) -> dict:
    """Resumable upload of an approved package. Returns the video id."""
    http = http or httpx.Client(timeout=120)
    with session() as s:
        job = s.get(UploadJob, upload_job_id)
        ex = s.get(VideoExport, job.export_id)
        ap = latest_approval(s, ex.id)
        if not ap or ap.decision != "approved":
            raise ApprovalRequired("not approved")
        if job.status == "uploaded" and job.youtube_video_id:
            return {"youtube_video_id": job.youtube_video_id, "duplicate": True}
        dup = s.scalar(select(PublishedVideo).join(ClipProject, PublishedVideo.project_id == ClipProject.id)
                       .where(ClipProject.id == ex.project_id))
        if dup:
            return {"youtube_video_id": dup.youtube_video_id, "duplicate": True}
        if not (s.get(ClipProject, ex.project_id).qc or {}).get("checks", {}).get("not_a_simulation", True):
            raise ApprovalRequired("this render uses simulated chat (demo) and can't be uploaded")
        costs.check_quota(s, 1600 + 50 + 50)
        meta = json.loads((pathlib.Path(job.package_dir) / "upload.json").read_text(encoding="utf-8"))
        video = pathlib.Path(job.package_dir) / pathlib.Path(ex.path).name
        resumable, sent = job.resumable_uri, job.bytes_sent
        job.status = "uploading"
    token = youtube_oauth_token()
    if not token:
        raise PermissionError("YouTube channel not connected (Settings > Connections)")
    hdr = {"Authorization": f"Bearer {token}"}
    size = video.stat().st_size
    if not resumable:
        status = {"privacyStatus": meta["privacyStatus"], "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
        if meta.get("publishAt") and meta["privacyStatus"] == "private":
            status["publishAt"] = meta["publishAt"]  # scheduled: private until then
        r = http.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={**hdr, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                      json={"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                                        "categoryId": meta["categoryId"]}, "status": status})
        if r.status_code >= 400:
            _fail(upload_job_id, r.text)
            raise RuntimeError(f"upload init failed {r.status_code}: {r.text[:300]}")
        resumable, sent = r.headers["Location"], 0
        with session() as s:
            s.get(UploadJob, upload_job_id).resumable_uri = resumable
    else:  # resume: ask the server how much it has
        r = http.put(resumable, headers={**hdr, "Content-Range": f"bytes */{size}"})
        if r.status_code == 308 and "Range" in r.headers:
            sent = int(r.headers["Range"].split("-")[1]) + 1
        elif r.status_code in (200, 201):
            return _finish(upload_job_id, r.json(), meta, hdr, http)
    body = None
    with open(video, "rb") as f:
        while sent < size:
            f.seek(sent)
            data = f.read(chunk)
            end = sent + len(data) - 1
            r = http.put(resumable, content=data, headers={**hdr, "Content-Length": str(len(data)),
                                                             "Content-Range": f"bytes {sent}-{end}/{size}"})
            if r.status_code == 308:
                sent = int(r.headers["Range"].split("-")[1]) + 1 if "Range" in r.headers else sent
            elif r.status_code in (200, 201):
                sent, body = size, r.json()
            else:
                _fail(upload_job_id, r.text, sent)
                raise RuntimeError(f"upload chunk failed {r.status_code}; resumable - retry the job")
            with session() as s:
                s.get(UploadJob, upload_job_id).bytes_sent = sent
    return _finish(upload_job_id, body, meta, hdr, http)


def _fail(jid: int, err: str, sent: int | None = None) -> None:
    with session() as s:
        j = s.get(UploadJob, jid)
        j.status, j.error = "failed", err[:2000]
        if sent is not None:
            j.bytes_sent = sent


def _finish(jid: int, body: dict, meta: dict, hdr: dict, http: httpx.Client) -> dict:
    vid = body["id"]
    notes = []
    with session() as s:
        job = s.get(UploadJob, jid)
        ex = s.get(VideoExport, job.export_id)
        costs.record(s, "youtube_data", "videos.insert", 1600)
        thumb = ex.thumb_path
    if thumb and pathlib.Path(thumb).exists():
        r = http.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": vid},
                      headers={**hdr, "Content-Type": "image/jpeg"}, content=pathlib.Path(thumb).read_bytes())
        notes.append("thumbnail set" if r.status_code < 400 else f"thumbnail not set ({r.status_code}; needs a verified channel)")
        with session() as s:
            costs.record(s, "youtube_data", "thumbnails.set", 50)
    with session() as s:
        job = s.get(UploadJob, jid)
        ex = s.get(VideoExport, job.export_id)
        if job.playlist_id:
            r = http.post(f"{API}/playlistItems", params={"part": "snippet"}, headers=hdr,
                          json={"snippet": {"playlistId": job.playlist_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}})
            notes.append("added to playlist" if r.status_code < 400 else f"playlist add failed ({r.status_code})")
            costs.record(s, "youtube_data", "playlistItems.insert", 50)
        job.status, job.youtube_video_id, job.error = "uploaded", vid, None
        p = s.get(ClipProject, ex.project_id)
        p.status = "uploaded"
        from .models import ViralCandidate
        c = s.get(ViralCandidate, p.candidate_id)
        c.production_status = "published" if meta["privacyStatus"] == "public" else "uploaded_private"
        s.add(PublishedVideo(youtube_video_id=vid, title=meta["title"], kind="short", published_at=now(), project_id=p.id,
                             attributes=production_attributes(s, p)))
        audit(s, "video_uploaded", vid, "owner", "security", privacy=meta["privacyStatus"])
    return {"youtube_video_id": vid, "notes": notes, "duplicate": False}


def production_attributes(s, p: ClipProject) -> dict:
    from .models import ProductionCost, ViralCandidate
    c = s.get(ViralCandidate, p.candidate_id)
    st = p.stages or {}
    pc = s.scalar(select(ProductionCost).where(ProductionCost.project_id == p.id))
    hooks = (st.get("commentary", {}).get("output", {}) or {}).get("hookscore", [])
    return {"candidate_score": c.overall_score, "stream_source": c.source_url, "topic": c.dominant_reaction,
            "clip_duration": (st.get("timeline", {}).get("output", {}) or {}).get("clip_duration"),
            "hook_type": (max(hooks, key=lambda h: h.get("score") or 0).get("formula") if hooks else None),
            "narration_style": (st.get("narration", {}).get("output", {}) or {}).get("provider"),
            "caption_preset": "plate", "editing_preset": p.preset, "structure": p.structure,
            "production_cost_usd": pc.actual_usd if pc else None}


# ---------------------------------------------------------------- analytics
METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes,comments,shares"


def fetch_analytics(video_ids: list[str], days: int = 28, http: httpx.Client | None = None) -> dict:
    token = youtube_oauth_token()
    if not token:
        return {"status": "NOT_CONNECTED", "rows": 0}
    http = http or httpx.Client(timeout=30)
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    hdr = {"Authorization": f"Bearer {token}"}
    stored = 0
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        r = http.get(ANALYTICS, headers=hdr, params={"ids": "channel==MINE", "startDate": start.isoformat(),
                                                    "endDate": end.isoformat(), "metrics": METRICS, "dimensions": "video",
                                                    "filters": "video==" + ",".join(chunk), "maxResults": 50})
        if r.status_code >= 400:
            return {"status": "ERROR", "detail": r.text[:300], "rows": stored}
        d = r.json()
        cols = [h["name"] for h in d.get("columnHeaders", [])]
        with session() as s:
            costs.record(s, "youtube_analytics", "reports.query", 1)
            for row in d.get("rows", []):
                rec = dict(zip(cols, row))
                vid = rec.pop("video")
                snap = s.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.youtube_video_id == vid,
                                                                AnalyticsSnapshot.day == end.isoformat()))
                if snap is None:
                    snap = AnalyticsSnapshot(youtube_video_id=vid, day=end.isoformat())
                    s.add(snap)
                snap.metrics = {**rec, "window_days": days}
                stored += 1
        for vid in chunk:  # traffic sources per video (Shorts feed shows up as SHORTS)
            r2 = http.get(ANALYTICS, headers=hdr, params={"ids": "channel==MINE", "startDate": start.isoformat(),
                                                         "endDate": end.isoformat(), "metrics": "views",
                                                         "dimensions": "insightTrafficSourceType", "filters": f"video=={vid}"})
            if r2.status_code < 400:
                ts = {row[0]: row[1] for row in r2.json().get("rows", [])}
                with session() as s:
                    snap = s.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.youtube_video_id == vid,
                                                                    AnalyticsSnapshot.day == end.isoformat()))
                    if snap:
                        snap.metrics = {**snap.metrics, "traffic_sources": ts}
                    costs.record(s, "youtube_analytics", "reports.query", 1)
    return {"status": "OK", "rows": stored}


def connection_status() -> list[dict]:
    from .chat.twitch import TwitchClient
    with session() as s:
        yt = s.scalar(select(ConnectedPlatform).where(ConnectedPlatform.platform == "youtube"))
    out = [{"platform": "YouTube channel (OAuth)", "status": yt.status if yt else ("READY_TO_CONNECT" if client_config() else "NOT_CONFIGURED"),
            "detail": (yt.detail if yt else (f"save OAuth client JSON to {settings.youtube_client_secrets}" if not client_config() else "click Connect")),
            "account": yt.account_name if yt else None},
           {"platform": "YouTube Data API key", "status": "CONFIGURED" if settings.youtube_api_key else "NOT_CONFIGURED",
            "detail": "YOUTUBE_API_KEY in .env (discovery + public chat)"},
           {"platform": "Twitch (Helix)", "status": "CONFIGURED" if TwitchClient().configured else "NOT_CONFIGURED",
            "detail": "TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET"},
           {"platform": "Twitch chat (EventSub)", "status": "CONFIGURED" if TwitchClient().chat_configured else "NOT_CONFIGURED",
            "detail": "TWITCH_USER_TOKEN (user:read:chat) + TWITCH_USER_ID"},
           {"platform": "ElevenLabs TTS", "status": "CONFIGURED" if settings.elevenlabs_key else "NOT_CONFIGURED",
            "detail": "voice Brian; paid calls only when enabled in Automation"},
           {"platform": "Anthropic (Claude)", "status": "CONFIGURED" if settings.anthropic_key else "NOT_CONFIGURED",
            "detail": f"model {settings.llm_model}; local writer used otherwise"}]
    return out
