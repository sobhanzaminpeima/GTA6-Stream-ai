"""Engine, sessions, versioned migrations, audit/notify helpers."""
from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import AuditLog, AutomationSettings, Base, Notification, SchemaVersion

_engines: dict[str, Any] = {}
_makers: dict[str, sessionmaker] = {}


def engine(url: str | None = None):
    url = url or settings.database_url
    if url not in _engines:
        kw: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        eng = create_engine(url, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(eng, "connect")
            def _pragmas(conn, _rec):  # WAL = concurrent reader + writer threads
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _engines[url] = eng
        _makers[url] = sessionmaker(bind=eng, expire_on_commit=False)
    return _engines[url]


def use_database(url: str) -> None:
    """Point the app at another DB (tests use a temp file)."""
    settings.env["STUDIO_DATABASE_URL"] = url
    engine(url)


@contextlib.contextmanager
def session() -> Iterator[Session]:
    engine()
    s = _makers[settings.database_url]()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---------------------------------------------------------------- migrations
# Each migration is (version, description, callable(conn)). Version 1 creates the
# full schema from models; later entries ALTER existing installs without data loss.
def _m1(conn):
    Base.metadata.create_all(conn)


def _m2_add_missing_columns(conn):
    """Forward-compatible: add any model column missing from an older table."""
    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            table.create(conn)
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                ctype = col.type.compile(dialect=conn.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ctype}'))


MIGRATIONS = [(1, "initial schema", _m1), (2, "add missing columns", _m2_add_missing_columns)]


def migrate() -> int:
    eng = engine()
    with eng.begin() as conn:
        SchemaVersion.__table__.create(conn, checkfirst=True)
        cur = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar() or 0
        for v, _desc, fn in MIGRATIONS:
            if v > cur:
                fn(conn)
                conn.execute(text("INSERT INTO schema_version (version, applied_at) VALUES (:v, CURRENT_TIMESTAMP)"), {"v": v})
                cur = v
        # always run the column check so model additions reach existing DBs
        _m2_add_missing_columns(conn)
    return cur


# ---------------------------------------------------------------- helpers
def audit(s: Session, action: str, target: str | None = None, actor: str = "system",
          severity: str = "info", **detail: Any) -> None:
    s.add(AuditLog(action=action, target=target, actor=actor, severity=severity,
                   detail=json.loads(json.dumps(detail, default=str))))


def notify(s: Session, title: str, body: str = "", level: str = "info", link: str | None = None) -> None:
    s.add(Notification(title=title, body=body, level=level, link=link))
    from . import events
    events.publish("notification", {"title": title, "body": body, "level": level, "link": link})


DEFAULT_AUTOMATION: dict[str, Any] = {
    "monitoring_window": {"start": "00:00", "end": "23:59"},
    "spike_sensitivity": 3.5,          # robust z-score threshold
    "min_messages_per_window": 8,      # in the 10 s window, before a spike can fire
    "merge_gap_s": 45,                 # spikes closer than this join one candidate
    "keyword_weights": {},             # overrides, e.g. {"clip": 2.0}
    "min_candidate_score": 60,
    "max_candidates_per_hour": 6,
    "recording_enabled": True,         # still requires per-source rights
    "buffer_minutes": 10,
    "pre_roll_s": 15,
    "post_roll_s": 25,
    "auto_generate_drafts": False,
    "daily_budget_usd": 3.0,
    "monthly_budget_usd": 40.0,
    "allow_paid_llm": False,           # paid calls only after the owner enables them
    "allow_paid_tts": False,
    "notifications": True,
    "publishing": {"default_privacy": "private", "require_approval": True},
    "score_weights": {"chat": 0.30, "audio": 0.20, "visual": 0.20, "narrative": 0.20, "originality": 0.10},
    "chat_latency_s": 6.0,             # viewer delay + reaction time, calibratable
    "narrator_preset": {"provider": "elevenlabs", "voice": "Brian", "speed": 1.05},
}


def automation(s: Session) -> dict[str, Any]:
    row = s.get(AutomationSettings, 1)
    if row is None:
        row = AutomationSettings(id=1, data=dict(DEFAULT_AUTOMATION))
        s.add(row)
        s.flush()
    merged = {**DEFAULT_AUTOMATION, **(row.data or {})}
    return merged


def set_automation(s: Session, patch: dict[str, Any]) -> dict[str, Any]:
    cur = automation(s)
    for k, v in patch.items():
        if k not in DEFAULT_AUTOMATION:
            raise ValueError(f"unknown setting: {k}")
        cur[k] = v
    row = s.get(AutomationSettings, 1)
    row.data = cur
    return cur
