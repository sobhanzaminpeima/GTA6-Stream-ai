"""Media-rights registry and the single gate every media operation must pass.

Rules (RULES.md "Rights"): public != licensed. Chat monitoring and media capture are
separate permissions. Without a valid OWNED / EXPLICITLY_LICENSED /
AUTHORIZED_FOR_CLIPPING record (with recording + editing rights, unexpired) the
system keeps only the source URL and a timestamp, never the media.

A permission record is not a YouTube monetization guarantee; reused-content risk is
reported separately (see `reuse_risk`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import audit
from .models import Livestream, RightsPermission, RightsState, now

CAPTURE_STATES = {RightsState.OWNED.value, RightsState.EXPLICITLY_LICENSED.value,
                  RightsState.AUTHORIZED_FOR_CLIPPING.value}


class RightsError(PermissionError):
    """Raised when code tries to acquire or edit media without authorization."""


@dataclass
class RightsDecision:
    state: str
    can_record: bool
    can_edit: bool
    can_publish_youtube: bool
    commercial_use: bool
    attribution: str | None
    permission_id: int | None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


def _valid(p: RightsPermission, at: dt.datetime) -> bool:
    return p.expires_at is None or p.expires_at > at


def lookup(s: Session, stream: Livestream) -> RightsPermission | None:
    """Most specific valid record wins: exact source URL, then the streamer's channel-wide record."""
    at = now()
    q = select(RightsPermission).where(or_(
        RightsPermission.source_url == stream.source_url,
        (RightsPermission.streamer_id == stream.streamer_id) if stream.streamer_id else False,
    )).order_by(RightsPermission.source_url.is_(None), RightsPermission.id.desc())
    for p in s.scalars(q):
        if _valid(p, at):
            return p
    return None


def decide(s: Session, stream: Livestream) -> RightsDecision:
    p = lookup(s, stream)
    if p is None:
        return RightsDecision(RightsState.UNKNOWN.value, False, False, False, False, None, None,
                              ["no permission record for this source or streamer"])
    reasons = []
    ok_state = p.state in CAPTURE_STATES
    if not ok_state:
        reasons.append(f"rights state is {p.state}")
    if p.state != RightsState.OWNED.value and not (p.evidence or "").strip():
        ok_state = False
        reasons.append("no authorization evidence recorded")
    owned = p.state == RightsState.OWNED.value
    can_record = ok_state and (owned or p.recording_allowed)
    can_edit = ok_state and (owned or p.editing_allowed)
    if ok_state and not can_record:
        reasons.append("recording not granted")
    if ok_state and not can_edit:
        reasons.append("editing not granted")
    yt = can_edit and "youtube" in [x.lower() for x in (p.platforms_allowed or [])]
    if can_edit and not yt:
        reasons.append("YouTube distribution not granted")
    commercial = owned or p.commercial_use
    if yt and not commercial:
        reasons.append("non-commercial only: do not monetize")
    return RightsDecision(p.state, can_record, can_edit, yt, commercial, p.attribution_required, p.id,
                          reasons or ["authorized"])


def require_capture(s: Session, stream: Livestream, actor: str = "system") -> RightsDecision:
    d = decide(s, stream)
    if not d.can_record:
        audit(s, "media_capture_blocked", f"livestream:{stream.id}", actor, "security", reasons=d.reasons)
        raise RightsError(f"media capture blocked for '{stream.channel_name}': {'; '.join(d.reasons)}")
    return d


def require_edit(s: Session, stream: Livestream, actor: str = "system") -> RightsDecision:
    d = decide(s, stream)
    if not d.can_edit:
        audit(s, "media_edit_blocked", f"livestream:{stream.id}", actor, "security", reasons=d.reasons)
        raise RightsError(f"editing blocked for '{stream.channel_name}': {'; '.join(d.reasons)}")
    return d


def reuse_risk(d: RightsDecision, has_original_commentary: bool, source_share: float) -> dict:
    """Separate from rights: YouTube's reused-content policy looks at added value.
    source_share = fraction of the final video that is unaltered source footage."""
    risk, notes = "low", []
    if d.state != RightsState.OWNED.value:
        risk = "medium"
        notes.append("third-party footage: monetization review can still flag reused content")
    if not has_original_commentary:
        risk = "high"
        notes.append("no original commentary: captions or an AI voice alone are not transformative")
    if source_share > 0.7:
        risk = "high" if risk != "low" else "medium"
        notes.append(f"{source_share:.0%} of the runtime is unaltered source footage")
    return {"risk": risk, "notes": notes or ["owned footage with original commentary"]}
