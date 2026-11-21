"""Database schema (SQLAlchemy 2.x). SQLite by default, PostgreSQL-compatible.

Schema changes go through studio/migrations.py (versioned, idempotent).
"""
from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------- enums
class RightsState(str, enum.Enum):
    OWNED = "OWNED"
    EXPLICITLY_LICENSED = "EXPLICITLY_LICENSED"
    AUTHORIZED_FOR_CLIPPING = "AUTHORIZED_FOR_CLIPPING"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


class ContentType(str, enum.Enum):
    CONFIRMED_GAMEPLAY = "confirmed_gameplay"
    GTA_V_GAMEPLAY = "gta_v_gameplay"
    GTA_VI_COMMENTARY = "gta_vi_commentary"
    TRAILER_REACTION = "trailer_reaction"
    FAN_CONCEPT = "fan_concept"
    UNVERIFIED = "unverified"


class MonitorStatus(str, enum.Enum):
    IDLE = "IDLE"
    CONNECTED = "CONNECTED"
    MONITORING = "MONITORING"
    RECONNECTING = "RECONNECTING"
    CHAT_UNAVAILABLE = "CHAT_UNAVAILABLE"
    SOURCE_RESTRICTED = "SOURCE_RESTRICTED"
    BUFFERING = "BUFFERING"
    PROCESSING = "PROCESSING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


# ---------------------------------------------------------------- accounts
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | editor | viewer
    api_token_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ConnectedPlatform(Base):
    __tablename__ = "connected_platforms"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), unique=True)  # youtube | twitch | elevenlabs | anthropic
    account_name: Mapped[str | None] = mapped_column(String(200))
    account_id: Mapped[str | None] = mapped_column(String(200))
    token_encrypted: Mapped[str | None] = mapped_column(Text)  # Fernet, never plaintext
    scopes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="NOT_CONFIGURED")
    last_checked: Mapped[dt.datetime | None] = mapped_column(DateTime)
    detail: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- sources
class Streamer(Base):
    __tablename__ = "streamers"
    __table_args__ = (UniqueConstraint("platform", "channel_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(20))
    channel_id: Mapped[str] = mapped_column(String(200))
    channel_url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(10), default="en")
    content_category: Mapped[str] = mapped_column(String(40), default=ContentType.UNVERIFIED.value)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 high .. 5 low
    active_monitoring: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"days":[0..6],"start":"18:00","end":"23:00"}
    custom_keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    last_monitored_stream_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    rights: Mapped[list["RightsPermission"]] = relationship(back_populates="streamer")


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    streamer_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)


class RightsPermission(Base):
    """Media rights for a streamer (channel-wide) or for one source URL."""
    __tablename__ = "rights_permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id"))
    source_url: Mapped[str | None] = mapped_column(String(500), index=True)
    state: Mapped[str] = mapped_column(String(40), default=RightsState.UNKNOWN.value)
    content_owner: Mapped[str | None] = mapped_column(String(200))
    evidence: Mapped[str | None] = mapped_column(Text)  # email / contract / campaign-terms link
    commercial_use: Mapped[bool] = mapped_column(Boolean, default=False)
    editing_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_required: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    platforms_allowed: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["youtube"])
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    streamer: Mapped[Streamer | None] = relationship(back_populates="rights")


class Livestream(Base):
    __tablename__ = "livestreams"
    __table_args__ = (UniqueConstraint("platform", "stream_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    stream_id: Mapped[str] = mapped_column(String(200))
    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id"))
    title: Mapped[str] = mapped_column(String(500), default="")
    channel_name: Mapped[str] = mapped_column(String(200), default="")
    channel_id: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(500), default="")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    language: Mapped[str | None] = mapped_column(String(10))
    viewer_count: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(120))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    is_live: Mapped[bool] = mapped_column(Boolean, default=True)
    chat_available: Mapped[bool | None] = mapped_column(Boolean)
    live_chat_id: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(40), default=ContentType.UNVERIFIED.value)
    classification_reason: Mapped[str | None] = mapped_column(Text)
    media_source: Mapped[str | None] = mapped_column(String(500))  # ingest URL/path ONLY for authorized sources
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)


class MonitoringSession(Base):
    __tablename__ = "monitoring_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    livestream_id: Mapped[int] = mapped_column(ForeignKey("livestreams.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=MonitorStatus.IDLE.value)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    buffer_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    buffer_minutes: Mapped[float] = mapped_column(Float, default=10.0)
    status_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    messages_seen: Mapped[int] = mapped_column(Integer, default=0)
    reconnects: Mapped[int] = mapped_column(Integer, default=0)
    chat_cursor: Mapped[str | None] = mapped_column(String(300))  # nextPageToken / resume point
    heartbeat: Mapped[dt.datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- chat signals
class ChatEvent(Base):
    __tablename__ = "chat_events"
    __table_args__ = (UniqueConstraint("session_id", "platform_msg_id"), Index("ix_chat_session_ts", "session_id", "ts"))
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"))
    platform: Mapped[str] = mapped_column(String(20))
    platform_msg_id: Mapped[str] = mapped_column(String(200))
    ts: Mapped[float] = mapped_column(Float)  # unix seconds (platform publish time)
    stream_ts: Mapped[float | None] = mapped_column(Float)
    content: Mapped[str] = mapped_column(Text)  # cleared by retention job
    normalized: Mapped[str] = mapped_column(Text)
    user_hash: Mapped[str] = mapped_column(String(32))  # salted hash, no raw user id stored
    emoji_count: Mapped[int] = mapped_column(Integer, default=0)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reaction_weight: Mapped[float] = mapped_column(Float, default=0.0)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)
    spam_reason: Mapped[str | None] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(30), default="message")  # message | raid | giveaway | mod | system


class ChatWindow(Base):
    """Per-second aggregate used by the timeline chart."""
    __tablename__ = "chat_windows"
    __table_args__ = (UniqueConstraint("session_id", "second"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"), index=True)
    second: Mapped[int] = mapped_column(Integer)  # unix second
    messages: Mapped[int] = mapped_column(Integer, default=0)
    unique_users: Mapped[int] = mapped_column(Integer, default=0)
    reaction: Mapped[float] = mapped_column(Float, default=0.0)
    emojis: Mapped[int] = mapped_column(Integer, default=0)
    spam: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)  # combined anomaly z
    audio: Mapped[float | None] = mapped_column(Float)
    visual: Mapped[float | None] = mapped_column(Float)


class SignalBaseline(Base):
    __tablename__ = "signal_baselines"
    __table_args__ = (UniqueConstraint("session_id", "metric"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"))
    metric: Mapped[str] = mapped_column(String(40))
    ewma: Mapped[float] = mapped_column(Float, default=0.0)
    ewvar: Mapped[float] = mapped_column(Float, default=0.0)
    median: Mapped[float] = mapped_column(Float, default=0.0)
    mad: Mapped[float] = mapped_column(Float, default=0.0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class DetectedSpike(Base):
    __tablename__ = "detected_spikes"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"), index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("viral_candidates.id"))
    ts: Mapped[float] = mapped_column(Float)
    anomaly_score: Mapped[float] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dominant_reaction: Mapped[str | None] = mapped_column(String(60))
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    suppress_reason: Mapped[str | None] = mapped_column(String(120))
    explanation: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- candidates
class ViralCandidate(Base):
    __tablename__ = "viral_candidates"
    __table_args__ = (Index("ix_cand_score", "overall_score"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"), index=True)
    livestream_id: Mapped[int] = mapped_column(ForeignKey("livestreams.id"))
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    event_ts: Mapped[float] = mapped_column(Float)  # wall-clock (chat-derived) event time
    first_spike_ts: Mapped[float] = mapped_column(Float)
    last_spike_ts: Mapped[float] = mapped_column(Float)
    media_ts: Mapped[float | None] = mapped_column(Float)  # estimated time in the source media
    media_ts_uncertainty: Mapped[float | None] = mapped_column(Float)  # +/- seconds
    source_url: Mapped[str] = mapped_column(String(500), default="")
    source_timestamp_url: Mapped[str | None] = mapped_column(String(600))
    description: Mapped[str] = mapped_column(Text, default="")
    dominant_reaction: Mapped[str | None] = mapped_column(String(60))
    chat_score: Mapped[float] = mapped_column(Float, default=0.0)
    audio_score: Mapped[float | None] = mapped_column(Float)
    visual_score: Mapped[float | None] = mapped_column(Float)
    narrative_score: Mapped[float | None] = mapped_column(Float)
    originality_score: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(20), default="low")
    tier: Mapped[str] = mapped_column(String(30), default="Low priority")
    rights_state: Mapped[str] = mapped_column(String(40), default=RightsState.UNKNOWN.value)
    clip_readiness: Mapped[str] = mapped_column(String(40), default="report_only")  # report_only|awaiting_post_roll|clip_ready|no_media
    production_status: Mapped[str] = mapped_column(String(40), default="new")  # new|saved|dismissed|in_production|ready_for_approval|approved|published
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    spike_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CandidateScore(Base):
    """History of scoring passes (chat-only first, re-scored once audio/visual arrive)."""
    __tablename__ = "candidate_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("viral_candidates.id"), index=True)
    weights: Mapped[dict[str, Any]] = mapped_column(JSON)
    components: Mapped[dict[str, Any]] = mapped_column(JSON)
    overall: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class TimestampMapping(Base):
    __tablename__ = "timestamp_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"), index=True)
    wall_ts: Mapped[float] = mapped_column(Float)  # local clock when a media segment started
    media_ts: Mapped[float] = mapped_column(Float)  # position in the source media timeline
    source: Mapped[str] = mapped_column(String(30))  # segment | stream_start | manual_calibration
    chat_latency_s: Mapped[float] = mapped_column(Float, default=0.0)  # viewer delay + reaction time
    uncertainty_s: Mapped[float] = mapped_column(Float, default=3.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- media
class MediaSegment(Base):
    __tablename__ = "media_segments"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("monitoring_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(600))
    media_start: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    wall_start: Mapped[float] = mapped_column(Float)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    pinned_by: Mapped[list[Any]] = mapped_column(JSON, default=list)  # job ids holding this segment
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_rms: Mapped[float | None] = mapped_column(Float)
    audio_score: Mapped[float | None] = mapped_column(Float)
    visual_score: Mapped[float | None] = mapped_column(Float)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # source_upload | candidate_clip | narration | music | render | cover | subtitle
    path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    rights_id: Mapped[int | None] = mapped_column(ForeignKey("rights_permissions.id"))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("viral_candidates.id"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- production
class ClipProject(Base):
    __tablename__ = "clip_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("viral_candidates.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    preset: Mapped[str] = mapped_column(String(40), default="CINEMATIC")
    structure: Mapped[str] = mapped_column(String(30), default="SURPRISE")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    stage: Mapped[str | None] = mapped_column(String(40))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    folder: Mapped[str] = mapped_column(String(600), default="")
    qc: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # stage -> {status, output, at}
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ClipTimeline(Base):
    __tablename__ = "clip_timelines"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    cuts: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{label, in, out}]
    selected_cut: Mapped[str] = mapped_column(String(10), default="B")
    reframe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    overlays: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class AIScript(Base):
    __tablename__ = "ai_scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(60))
    context_summary: Mapped[str] = mapped_column(Text, default="")
    hooks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    structure: Mapped[str] = mapped_column(String(30), default="SURPRISE")
    script: Mapped[str] = mapped_column(Text, default="")
    graphics: Mapped[list[Any]] = mapped_column(JSON, default=list)
    payoff: Mapped[str] = mapped_column(Text, default="")
    claims: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{claim, evidence}]
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Voiceover(Base):
    __tablename__ = "voiceovers"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    voice: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(String(600))
    words: Mapped[list[Any]] = mapped_column(JSON, default=list)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    timing_source: Mapped[str] = mapped_column(String(30), default="provider")  # provider | estimated
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class SubtitleTrack(Base):
    __tablename__ = "subtitle_tracks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    srt_path: Mapped[str] = mapped_column(String(600))
    ass_path: Mapped[str] = mapped_column(String(600))
    cues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    command: Mapped[str | None] = mapped_column(Text)
    log_tail: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class VideoExport(Base):
    __tablename__ = "video_exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), index=True)
    path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float)
    video_codec: Mapped[str] = mapped_column(String(20))
    audio_codec: Mapped[str] = mapped_column(String(20))
    loudness_lufs: Mapped[float | None] = mapped_column(Float)
    cover_path: Mapped[str | None] = mapped_column(String(600))
    thumb_path: Mapped[str | None] = mapped_column(String(600))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ApprovalRecord(Base):
    __tablename__ = "approval_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    export_id: Mapped[int] = mapped_column(ForeignKey("video_exports.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected | changes_requested
    visibility_approved: Mapped[str] = mapped_column(String(20), default="private")  # private | unlisted | public
    approver: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class UploadJob(Base):
    __tablename__ = "upload_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    export_id: Mapped[int] = mapped_column(ForeignKey("video_exports.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)  # sha256(file)+channel
    status: Mapped[str] = mapped_column(String(30), default="prepared")  # prepared|uploading|uploaded|failed
    privacy: Mapped[str] = mapped_column(String(20), default="private")
    publish_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    playlist_id: Mapped[str | None] = mapped_column(String(80))
    youtube_video_id: Mapped[str | None] = mapped_column(String(40))
    resumable_uri: Mapped[str | None] = mapped_column(Text)
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    package_dir: Mapped[str | None] = mapped_column(String(600))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class PublishedVideo(Base):
    __tablename__ = "published_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), default="short")
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("clip_projects.id"))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # hook_type, preset, duration, topic, cost...


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (UniqueConstraint("youtube_video_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(40), index=True)
    day: Mapped[str] = mapped_column(String(10))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # only metrics the API returned
    source: Mapped[str] = mapped_column(String(30), default="youtube_analytics_api")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class GrowthRecommendation(Base):
    __tablename__ = "growth_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    week: Mapped[str] = mapped_column(String(10), index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("clip_projects.id"))
    title: Mapped[str] = mapped_column(String(300))
    planned_for: Mapped[dt.datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- ops
class AutomationSettings(Base):
    __tablename__ = "automation_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (Index("ix_usage_provider_day", "provider", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))  # youtube_data | youtube_analytics | twitch | anthropic | elevenlabs | local
    operation: Mapped[str] = mapped_column(String(60))
    day: Mapped[str] = mapped_column(String(10))
    units: Mapped[float] = mapped_column(Float, default=0.0)  # quota units / tokens / characters
    unit_type: Mapped[str] = mapped_column(String(20), default="quota")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    project_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ProductionCost(Base):
    __tablename__ = "production_costs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("clip_projects.id"), unique=True)
    estimated_usd: Mapped[float] = mapped_column(Float, default=0.0)
    actual_usd: Mapped[float] = mapped_column(Float, default=0.0)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    render_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    storage_bytes: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    """Durable background job (DB-backed queue; survives worker crashes)."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status", "status", "run_after"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[float] = mapped_column(Float, default=0.0)
    locked_by: Mapped[str | None] = mapped_column(String(60))
    heartbeat: Mapped[float | None] = mapped_column(Float)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    actor: Mapped[str] = mapped_column(String(60), default="system")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str | None] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | warn | security
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    level: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(200))
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
