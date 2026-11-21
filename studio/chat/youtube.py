"""YouTube Data API v3: live discovery + live chat polling (official endpoints only).

Verified against developers.google.com (2026-11-20):
- liveChatMessages.list: GET /youtube/v3/liveChat/messages, part=snippet,authorDetails,
  5 quota units per call, honour pollingIntervalMillis, follow nextPageToken, stop on
  offlineAt / liveChatEnded (403); liveChatDisabled (403), liveChatNotFound (404),
  rateLimitExceeded -> back off.
- search.list (eventType=live) costs 100 units, so discovery is run on a schedule,
  not per minute. videos.list (liveStreamingDetails) costs 1 unit.
Auth: an API key works for public read calls; an OAuth token (from the connected
channel) is used when present, which also covers chats that need it.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Iterator

import httpx

from . import RawMessage
from ..config import settings

BASE = "https://www.googleapis.com/youtube/v3"
QUOTA = {"search.list": 100, "videos.list": 1, "liveChatMessages.list": 5, "channels.list": 1}


class YouTubeError(RuntimeError):
    def __init__(self, reason: str, status: int, msg: str = ""):
        super().__init__(f"{status} {reason}: {msg}")
        self.reason, self.status = reason, status


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class YouTubeClient:
    def __init__(self, api_key: str | None = None, oauth_token: str | None = None, http: httpx.Client | None = None,
                 usage_cb=None):
        self.api_key = api_key if api_key is not None else settings.youtube_api_key
        self.oauth_token = oauth_token
        self.http = http or httpx.Client(timeout=20)
        self.usage_cb = usage_cb  # (operation, units) -> None, records quota spend

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.oauth_token)

    def _get(self, path: str, op: str, **params) -> dict:
        if not self.configured:
            raise YouTubeError("notConfigured", 0, "set YOUTUBE_API_KEY in .env or connect the channel")
        headers = {}
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        else:
            params["key"] = self.api_key
        r = self.http.get(f"{BASE}/{path}", params=params, headers=headers)
        if self.usage_cb:
            self.usage_cb(op, QUOTA.get(op, 1))
        if r.status_code >= 400:
            try:
                err = r.json()["error"]
                reason = (err.get("errors") or [{}])[0].get("reason", "error")
                msg = err.get("message", "")
            except Exception:
                reason, msg = "http_error", r.text[:200]
            raise YouTubeError(reason, r.status_code, msg)
        return r.json()

    # ---- discovery
    def search_live(self, query: str, max_results: int = 25, language: str | None = "en", cc_only: bool = False) -> list[dict]:
        p = dict(part="snippet", eventType="live", type="video", q=query, maxResults=max_results, order="viewCount")
        if cc_only:  # creator chose Creative Commons Attribution: reuse + edit + commercial with credit
            p["videoLicense"] = "creativeCommon"
        if language:
            p["relevanceLanguage"] = language
        items = self._get("search", "search.list", **p).get("items", [])
        ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
        return self.video_details(ids) if ids else []

    def video_details(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            d = self._get("videos", "videos.list", part="snippet,liveStreamingDetails,statistics,status",
                          id=",".join(ids[i:i + 50]))
            for v in d.get("items", []):
                sn, ld = v.get("snippet", {}), v.get("liveStreamingDetails", {})
                out.append({
                    "platform": "youtube", "stream_id": v["id"], "title": sn.get("title", ""),
                    "channel_name": sn.get("channelTitle", ""), "channel_id": sn.get("channelId"),
                    "source_url": f"https://www.youtube.com/watch?v={v['id']}",
                    "started_at": _iso(ld.get("actualStartTime")),
                    "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                    "viewer_count": int(ld["concurrentViewers"]) if ld.get("concurrentViewers") else None,
                    "category": sn.get("categoryId"), "description": sn.get("description", "")[:2000],
                    "tags": sn.get("tags", []),
                    "thumbnail_url": (sn.get("thumbnails", {}).get("high") or {}).get("url"),
                    "is_live": sn.get("liveBroadcastContent") == "live" and not ld.get("actualEndTime"),
                    "live_chat_id": ld.get("activeLiveChatId"),
                    "chat_available": bool(ld.get("activeLiveChatId")),
                    "license": (v.get("status") or {}).get("license"),
                })
        return out

    def resolve_channel(self, ref: str) -> dict:
        """Accepts a channel URL, @handle or UC... id."""
        ref = ref.strip().rstrip("/")
        if "youtube.com/" in ref:
            ref = ref.split("youtube.com/")[1]
            if ref.startswith("channel/"):
                ref = ref.split("/")[1]
        if ref.startswith("UC") and len(ref) == 24:
            d = self._get("channels", "channels.list", part="snippet", id=ref)
        else:
            d = self._get("channels", "channels.list", part="snippet", forHandle=ref.lstrip("@").split("/")[0])
        items = d.get("items", [])
        if not items:
            raise YouTubeError("channelNotFound", 404, ref)
        c = items[0]
        return {"channel_id": c["id"], "name": c["snippet"]["title"], "language": c["snippet"].get("defaultLanguage") or "en",
                "channel_url": f"https://www.youtube.com/channel/{c['id']}"}

    # ---- chat
    def poll_chat(self, live_chat_id: str, page_token: str | None = None, stop=lambda: False,
                  on_cursor=None, max_backoff: float = 60.0) -> Iterator[RawMessage]:
        """Generator over new chat messages. Reconnects with exponential back-off on
        transient errors; raises YouTubeError on terminal ones (ended / disabled / not found)."""
        backoff = 1.0
        while not stop():
            try:
                d = self._get("liveChat/messages", "liveChatMessages.list", liveChatId=live_chat_id,
                              part="snippet,authorDetails", maxResults=2000, **({"pageToken": page_token} if page_token else {}))
            except YouTubeError as e:
                if e.reason in ("liveChatEnded", "liveChatDisabled", "liveChatNotFound", "forbidden", "notConfigured"):
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
            except httpx.HTTPError:
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
            backoff = 1.0
            for it in d.get("items", []):
                sn, au = it.get("snippet", {}), it.get("authorDetails", {})
                mtype = sn.get("type", "textMessageEvent")
                text = sn.get("displayMessage") or (sn.get("textMessageDetails") or {}).get("messageText", "")
                kind = "message" if mtype in ("textMessageEvent", "superChatEvent", "superStickerEvent") else "system"
                if au.get("isChatModerator") or au.get("isChatOwner"):
                    kind = "mod" if kind == "message" and text.startswith("!") else kind
                yield RawMessage("youtube", it["id"], _iso(sn.get("publishedAt")) or time.time(),
                                 au.get("channelId", "?"), text, False, kind)
            page_token = d.get("nextPageToken", page_token)
            if on_cursor:
                on_cursor(page_token)
            if d.get("offlineAt"):
                raise YouTubeError("liveChatEnded", 403, "stream went offline")
            wait = max(d.get("pollingIntervalMillis", 5000) / 1000.0, 1.0)
            t_end = time.time() + wait
            while time.time() < t_end and not stop():
                time.sleep(0.2)
