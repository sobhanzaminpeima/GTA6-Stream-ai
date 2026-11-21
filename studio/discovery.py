"""Stream Discovery Radar: find GTA live streams on YouTube + Twitch, classify content.

Classification is conservative: a title saying "GTA 6" is never enough to call it
confirmed GTA 6 gameplay. Only the platform category (Twitch "Grand Theft Auto VI") or
the owner's manual verification marks confirmed_gameplay.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select

from .chat.twitch import TwitchClient
from .chat.youtube import YouTubeClient
from .costs import check_quota, record
from .db import audit, session
from .models import ContentType, Livestream, RightsPermission, Streamer, now

DEFAULT_KEYWORDS = ["GTA VI", "GTA 6", "Grand Theft Auto", "Vice City", "Leonida", "GTA Gameplay", "GTA Livestream"]
TWITCH_CATEGORIES = ["Grand Theft Auto VI", "Grand Theft Auto V"]

R_VI = re.compile(r"\b(gta\s*(6|vi)|grand theft auto\s*(6|vi)|leonida|vice city 2026)\b", re.I)
R_V = re.compile(r"\b(gta\s*(5|v)\b|gtav|gta online|fivem|nopixel|gta\s*rp|roleplay|los santos)\b", re.I)
R_FAN = re.compile(r"\b(concept|fan[- ]?made|what if|unreal engine|ue5|ai generated|mod(ded|s)?|recreat)", re.I)
R_TRAILER = re.compile(r"\btrailer\b", re.I)
R_REACT = re.compile(r"\b(react(ion|s|ing)?|breakdown|analysis|watch party)\b", re.I)
R_TALK = re.compile(r"\b(news|leak|discussion|podcast|explained|talk|q&a|rumou?r|theor(y|ies)|price|edition)\b", re.I)
R_PLAY = re.compile(r"\b(gameplay|playthrough|walkthrough|first look|let'?s play|story mode|mission|heist|part \d+|day \d+)\b", re.I)


def classify(title: str, description: str = "", tags: list[str] | None = None, category: str | None = None) -> tuple[str, str]:
    text = " ".join([title or "", (description or "")[:600], " ".join(tags or [])])
    cat = (category or "").lower()
    if cat == "grand theft auto vi":
        return ContentType.CONFIRMED_GAMEPLAY.value, "platform category is Grand Theft Auto VI"
    if cat == "grand theft auto v":
        return ContentType.GTA_V_GAMEPLAY.value, "platform category is Grand Theft Auto V"
    vi, v = bool(R_VI.search(text)), bool(R_V.search(text))
    if R_FAN.search(title or ""):
        return ContentType.FAN_CONCEPT.value, "title mentions concept/fan-made/mod"
    if R_TRAILER.search(title or "") and (R_REACT.search(title or "") or vi):
        return ContentType.TRAILER_REACTION.value, "title mentions trailer + reaction/breakdown"
    if v and not vi:
        return ContentType.GTA_V_GAMEPLAY.value, "mentions GTA 5 / Online / RP and not GTA 6"
    if vi and R_TALK.search(title or ""):
        return ContentType.GTA_VI_COMMENTARY.value, "GTA 6 + news/discussion words"
    if vi and R_PLAY.search(title or ""):
        return ContentType.UNVERIFIED.value, "title claims GTA 6 gameplay - unverified until the category or owner confirms"
    if vi:
        return ContentType.UNVERIFIED.value, "mentions GTA 6 but content type unclear"
    return ContentType.UNVERIFIED.value, "no GTA 6 signal"


def upsert(s, d: dict, is_sim: bool = False) -> Livestream:
    ls = s.scalar(select(Livestream).where(Livestream.platform == d["platform"], Livestream.stream_id == d["stream_id"]))
    if ls is None:
        ls = Livestream(platform=d["platform"], stream_id=d["stream_id"], is_simulation=is_sim)
        s.add(ls)
    for k in ("title", "channel_name", "channel_id", "source_url", "language", "viewer_count", "category",
              "thumbnail_url", "is_live", "chat_available", "live_chat_id", "license"):
        if k in d and d[k] is not None:
            setattr(ls, k, d[k])
    if d.get("started_at"):
        ls.started_at = dt.datetime.fromtimestamp(d["started_at"], dt.timezone.utc).replace(tzinfo=None)
    if not (ls.classification_reason or "").startswith("owner"):  # owner's manual label always wins
        ls.content_type, ls.classification_reason = classify(d.get("title", ""), d.get("description", ""),
                                                             d.get("tags"), d.get("category"))
    ls.last_seen = now()
    st = s.scalar(select(Streamer).where(Streamer.platform == d["platform"], Streamer.channel_id == d.get("channel_id")))
    if st:
        ls.streamer_id = st.id
    if ls.license == "creativeCommon":
        record_cc_license(s, ls)
    return ls


def record_cc_license(s, ls: Livestream) -> None:
    """The creator published this stream under Creative Commons Attribution (CC BY), which
    allows reuse, editing and commercial use with credit. Record that as the evidence.
    Never overrides a record the owner made (e.g. RESTRICTED)."""
    if s.scalar(select(RightsPermission).where(RightsPermission.source_url == ls.source_url)):
        return
    s.add(RightsPermission(
        source_url=ls.source_url, state="EXPLICITLY_LICENSED", content_owner=ls.channel_name,
        evidence=f"YouTube license field status.license=creativeCommon (CC BY) for {ls.source_url}, "
                 f"read via YouTube Data API on {now():%Y-%m-%d}",
        commercial_use=True, editing_allowed=True, recording_allowed=True, platforms_allowed=["youtube"],
        attribution_required=f"Credit: {ls.channel_name} - {ls.source_url} (CC BY)",
        notes="Auto-recorded from the stream's public license. Re-check the license on the watch page before "
              "publishing; the creator can change it for future uploads."))
    audit(s, "cc_license_recorded", ls.source_url, "system", "security", channel=ls.channel_name)


def run_discovery(keywords: list[str] | None = None, language: str | None = "en", platforms=("youtube", "twitch"),
                  cc_only: bool = False) -> dict:
    """cc_only: YouTube only, streams the creator licensed as Creative Commons (CC BY)."""
    if cc_only:
        platforms = ("youtube",)
    keywords = keywords or DEFAULT_KEYWORDS
    report = {"youtube": {"status": "skipped"}, "twitch": {"status": "skipped"}, "found": 0}
    if "youtube" in platforms:
        def usage(op, units):
            with session() as s:
                record(s, "youtube_data", op, units)
        yt = YouTubeClient(usage_cb=usage)
        if not yt.configured:
            report["youtube"] = {"status": "NOT_CONFIGURED", "detail": "set YOUTUBE_API_KEY in .env"}
        else:
            found, errs = [], []
            for q in keywords[:4]:  # search.list = 100 units each: cap per run
                try:
                    with session() as s:
                        check_quota(s, 101)
                    found += yt.search_live(q, 25, language, cc_only=cc_only)
                except Exception as e:
                    errs.append(f"{q}: {e}")
            with session() as s:
                for d in {d["stream_id"]: d for d in found}.values():
                    upsert(s, d)
            cc = sum(1 for d in found if d.get("license") == "creativeCommon")
            report["youtube"] = {"status": "OK" if not errs else "PARTIAL", "streams": len(found), "cc_by": cc, "errors": errs[:3]}
            report["found"] += len(found)
    if "twitch" in platforms:
        tw = TwitchClient()
        if not tw.configured:
            report["twitch"] = {"status": "NOT_CONFIGURED", "detail": "set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET"}
        else:
            try:
                gids = []
                for name in TWITCH_CATEGORIES:
                    gids += [c["id"] for c in tw.find_categories(name) if c["name"].lower() == name.lower()]
                streams = tw.live_streams(gids, language) if gids else []
                with session() as s:
                    for d in streams:
                        upsert(s, d)
                report["twitch"] = {"status": "OK", "streams": len(streams), "categories": gids}
                report["found"] += len(streams)
            except Exception as e:
                report["twitch"] = {"status": "ERROR", "detail": str(e)[:300]}
    with session() as s:
        audit(s, "discovery_run", None, report=report)
    return report


def add_streamer(platform: str, ref: str, name: str | None = None, language: str = "en", priority: int = 3,
                 category: str = ContentType.UNVERIFIED.value, resolve: bool = True) -> int:
    """Watchlist add by URL / handle / id. Adding never starts media capture."""
    info = {"channel_id": ref.strip(), "name": name or ref.strip(), "language": language, "channel_url": ref.strip()}
    if resolve:
        try:
            if platform == "youtube" and YouTubeClient().configured:
                info = YouTubeClient().resolve_channel(ref)
            elif platform == "twitch" and TwitchClient().configured:
                info = TwitchClient().resolve_user(ref)
        except Exception as e:
            info["resolve_error"] = str(e)[:200]
    if not ref.startswith("http") and platform == "twitch" and "channel_url" in info and not info["channel_url"].startswith("http"):
        info["channel_url"] = f"https://www.twitch.tv/{ref.strip().lstrip('@')}"
    with session() as s:
        st = s.scalar(select(Streamer).where(Streamer.platform == platform, Streamer.channel_id == info["channel_id"]))
        if st is None:
            st = Streamer(platform=platform, channel_id=info["channel_id"], name=name or info["name"])
            s.add(st)
        st.channel_url, st.language, st.priority, st.content_category = info.get("channel_url"), info.get("language") or language, priority, category
        s.flush()
        audit(s, "streamer_added", f"streamer:{st.id}", "owner", platform=platform, ref=ref)
        return st.id
