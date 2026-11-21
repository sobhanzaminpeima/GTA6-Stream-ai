"""Twitch Helix discovery + EventSub WebSocket chat (official APIs only).

Verified against dev.twitch.tv (2026-11-20):
- WebSocket: wss://eventsub.wss.twitch.tv/ws. First frame session_welcome carries
  payload.session.id and keepalive_timeout_seconds; subscribe within 10 s. A silence
  longer than the keepalive window = dead connection -> reconnect + resubscribe.
  session_reconnect gives reconnect_url (use unmodified; subscriptions carry over).
  revocation = stop for that subscription.
- WebSocket subscriptions need a USER access token (app tokens fail). channel.chat.message
  v1, condition {broadcaster_user_id, user_id}, scope user:read:chat.
- Discovery (Helix /streams, /search/categories) works with an app token
  (client-credentials).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from typing import Iterator

import httpx

from . import RawMessage
from ..config import settings

HELIX = "https://api.twitch.tv/helix"
WS_URL = "wss://eventsub.wss.twitch.tv/ws"


class TwitchError(RuntimeError):
    pass


_TS = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?")


def parse_ts(s: str | None) -> float:
    """RFC 3339 with nanoseconds (Twitch) -> unix seconds."""
    m = _TS.match(s or "")
    if not m:
        return time.time()
    base = dt.datetime.fromisoformat(m[1] + ("+00:00" if m[3] in (None, "Z") else m[3]))
    return base.timestamp() + (float("0" + m[2]) if m[2] else 0.0)


class TwitchClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 user_token: str | None = None, user_id: str | None = None, http: httpx.Client | None = None,
                 usage_cb=None):
        self.client_id = client_id if client_id is not None else settings.twitch_client_id
        self.client_secret = client_secret if client_secret is not None else settings.twitch_client_secret
        self.user_token = user_token if user_token is not None else settings.get("TWITCH_USER_TOKEN")
        self.user_id = user_id if user_id is not None else settings.get("TWITCH_USER_ID")
        self.http = http or httpx.Client(timeout=20)
        self._app_token: tuple[str, float] | None = None
        self.usage_cb = usage_cb

    @property
    def configured(self) -> bool:
        return bool(self.client_id and (self.client_secret or self.user_token))

    @property
    def chat_configured(self) -> bool:
        return bool(self.client_id and self.user_token and self.user_id)

    def app_token(self) -> str:
        if self._app_token and self._app_token[1] > time.time() + 60:
            return self._app_token[0]
        if not (self.client_id and self.client_secret):
            if self.user_token:
                return self.user_token
            raise TwitchError("Twitch not configured: set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env")
        r = self.http.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"})
        r.raise_for_status()
        d = r.json()
        self._app_token = (d["access_token"], time.time() + d.get("expires_in", 3600))
        return d["access_token"]

    def _helix(self, method: str, path: str, token: str | None = None, **kw) -> dict:
        headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {token or self.app_token()}"}
        for attempt in range(4):
            r = self.http.request(method, f"{HELIX}/{path}", headers=headers, **kw)
            if self.usage_cb:
                self.usage_cb(path.split("?")[0], 1)
            if r.status_code == 429:  # rate limit: wait for the bucket to refill
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 2))
                time.sleep(max(0.5, min(reset - time.time(), 30)))
                continue
            if r.status_code >= 400:
                raise TwitchError(f"Helix {path} -> {r.status_code}: {r.text[:200]}")
            return r.json() if r.content else {}
        raise TwitchError(f"Helix {path}: rate limited")

    # ---- discovery
    def find_categories(self, query: str) -> list[dict]:
        return self._helix("GET", "search/categories", params={"query": query, "first": 10}).get("data", [])

    def live_streams(self, game_ids: list[str], language: str | None = "en", first: int = 50) -> list[dict]:
        params: list[tuple[str, str]] = [("game_id", g) for g in game_ids] + [("first", str(first)), ("type", "live")]
        if language:
            params.append(("language", language))
        out = []
        for s in self._helix("GET", "streams", params=params).get("data", []):
            out.append({
                "platform": "twitch", "stream_id": s["id"], "title": s.get("title", ""),
                "channel_name": s.get("user_name", ""), "channel_id": s.get("user_id"),
                "source_url": f"https://www.twitch.tv/{s.get('user_login', '')}",
                "started_at": dt.datetime.fromisoformat(s["started_at"].replace("Z", "+00:00")).timestamp() if s.get("started_at") else None,
                "language": s.get("language"), "viewer_count": s.get("viewer_count"),
                "category": s.get("game_name"), "tags": s.get("tags") or [],
                "thumbnail_url": (s.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360"),
                "is_live": s.get("type") == "live", "chat_available": True, "description": "",
            })
        return out

    def resolve_user(self, ref: str) -> dict:
        login = ref.strip().rstrip("/").split("twitch.tv/")[-1].split("/")[0].lstrip("@").lower()
        data = self._helix("GET", "users", params={"login": login}).get("data", [])
        if not data:
            raise TwitchError(f"Twitch user not found: {login}")
        u = data[0]
        return {"channel_id": u["id"], "name": u["display_name"], "language": "en",
                "channel_url": f"https://www.twitch.tv/{u['login']}"}

    # ---- chat (EventSub WebSocket)
    def chat_messages(self, broadcaster_id: str, stop=lambda: False, on_status=None) -> Iterator[RawMessage]:
        if not self.chat_configured:
            raise TwitchError("Twitch chat needs TWITCH_CLIENT_ID, TWITCH_USER_TOKEN (scope user:read:chat) and TWITCH_USER_ID")
        from websockets.sync.client import connect

        url, backoff, subscribed = WS_URL, 1.0, False
        while not stop():
            try:
                with connect(url, open_timeout=15) as ws:
                    keepalive = 10.0
                    last = time.time()
                    while not stop():
                        try:
                            raw = ws.recv(timeout=1.0)
                        except TimeoutError:
                            if time.time() - last > keepalive + 5:
                                raise ConnectionError("keepalive timeout")
                            continue
                        last = time.time()
                        m = json.loads(raw)
                        mt = m["metadata"]["message_type"]
                        if mt == "session_welcome":
                            sess = m["payload"]["session"]
                            keepalive = float(sess.get("keepalive_timeout_seconds") or 10)
                            if not subscribed:
                                self._subscribe(sess["id"], broadcaster_id)
                                subscribed = True
                            backoff = 1.0
                            if on_status:
                                on_status("MONITORING", "EventSub connected")
                        elif mt == "session_reconnect":
                            url = m["payload"]["session"]["reconnect_url"]
                            if on_status:
                                on_status("RECONNECTING", "Twitch moved the session")
                            break  # subscriptions carry over to the new URL
                        elif mt == "revocation":
                            raise TwitchError(f"subscription revoked: {m['payload']['subscription'].get('status')}")
                        elif mt == "notification":
                            ev = m["payload"]["event"]
                            stype = m["payload"]["subscription"]["type"]
                            ts = parse_ts(m["metadata"].get("message_timestamp"))
                            if stype == "channel.chat.message":
                                badges = {b.get("set_id") for b in ev.get("badges") or []}
                                yield RawMessage("twitch", ev["message_id"], ts, ev.get("chatter_user_id", "?"),
                                                 (ev.get("message") or {}).get("text", ""), "bot" in badges,
                                                 "mod" if "moderator" in badges and (ev.get("message") or {}).get("text", "").startswith("!") else "message")
                            elif stype == "channel.raid":
                                yield RawMessage("twitch", m["metadata"]["message_id"], ts, "raid",
                                                 f"RAID from {ev.get('from_broadcaster_user_name')} ({ev.get('viewers')} viewers)", False, "raid")
            except TwitchError:
                raise
            except Exception as e:  # network / keepalive: reconnect from scratch and resubscribe
                if on_status:
                    on_status("RECONNECTING", str(e)[:120])
                url, subscribed = WS_URL, False
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _subscribe(self, session_id: str, broadcaster_id: str) -> None:
        tr = {"method": "websocket", "session_id": session_id}
        self._helix("POST", "eventsub/subscriptions", token=self.user_token, json={
            "type": "channel.chat.message", "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id, "user_id": self.user_id}, "transport": tr})
        try:  # raid detection (used to suppress raid-driven spikes); optional
            self._helix("POST", "eventsub/subscriptions", token=self.user_token, json={
                "type": "channel.raid", "version": "1",
                "condition": {"to_broadcaster_user_id": broadcaster_id}, "transport": tr})
        except TwitchError:
            pass
