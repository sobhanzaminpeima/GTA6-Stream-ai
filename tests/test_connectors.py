"""YouTube + Twitch connectors against mocked official endpoints (no network)."""
import json
import threading
import time

import httpx
import pytest

from studio.chat import twitch as tw
from studio.chat.youtube import YouTubeClient, YouTubeError


def yt_transport(pages):
    calls = {"n": 0, "params": []}

    def handler(req: httpx.Request):
        p = dict(req.url.params)
        calls["params"].append(p)
        if req.url.path.endswith("/liveChat/messages"):
            i = calls["n"]
            calls["n"] += 1
            if i >= len(pages):
                return httpx.Response(403, json={"error": {"errors": [{"reason": "liveChatEnded"}], "message": "ended"}})
            return httpx.Response(200, json=pages[i])
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "vid1"}}]})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"id": "vid1", "snippet": {"title": "GTA 6 live", "channelTitle": "Chan",
                                                                                 "channelId": "UC1", "liveBroadcastContent": "live"},
                                                      "liveStreamingDetails": {"activeLiveChatId": "chat1", "concurrentViewers": "1234",
                                                                               "actualStartTime": "2026-11-20T10:00:00Z"}}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler), calls


def test_youtube_discovery_and_chat_polling():
    page = lambda tok, n: {"items": [{"id": f"m{tok}{i}", "snippet": {"type": "textMessageEvent", "displayMessage": "CLIP THAT",
                                                                       "publishedAt": "2026-11-20T10:05:00.5Z"},
                                      "authorDetails": {"channelId": f"user{i}"}} for i in range(n)],
                           "nextPageToken": f"tok{tok}", "pollingIntervalMillis": 1000}
    tr, calls = yt_transport([page(1, 3), page(2, 2)])
    usage = []
    yt = YouTubeClient(api_key="k", http=httpx.Client(transport=tr), usage_cb=lambda op, u: usage.append((op, u)))
    streams = yt.search_live("GTA 6")
    assert streams[0]["live_chat_id"] == "chat1" and streams[0]["viewer_count"] == 1234 and streams[0]["is_live"]
    got = []
    with pytest.raises(YouTubeError) as e:
        for m in yt.poll_chat("chat1"):
            got.append(m)
    assert e.value.reason == "liveChatEnded"
    assert [m.msg_id for m in got] == ["m10", "m11", "m12", "m20", "m21"]
    chat_calls = [p for p in calls["params"] if p.get("liveChatId")]
    assert chat_calls[1]["pageToken"] == "tok1"  # follows nextPageToken
    assert ("search.list", 100) in usage and ("liveChatMessages.list", 5) in usage


def test_youtube_not_configured():
    yt = YouTubeClient(api_key="", oauth_token=None)
    with pytest.raises(YouTubeError) as e:
        yt.search_live("x")
    assert e.value.reason == "notConfigured"


def test_twitch_helix_discovery():
    def handler(req):
        if req.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "app", "expires_in": 3600})
        if req.url.path.endswith("/search/categories"):
            return httpx.Response(200, json={"data": [{"id": "999", "name": "Grand Theft Auto VI"}]})
        if req.url.path.endswith("/streams"):
            assert req.headers["Client-Id"] == "cid" and req.headers["Authorization"] == "Bearer app"
            return httpx.Response(200, json={"data": [{"id": "s1", "user_id": "u1", "user_login": "streamer", "user_name": "Streamer",
                                                       "game_name": "Grand Theft Auto VI", "type": "live", "title": "heists",
                                                       "viewer_count": 4200, "language": "en", "started_at": "2026-11-20T10:00:00Z",
                                                       "thumbnail_url": "https://x/{width}x{height}.jpg"}]})
        return httpx.Response(404)
    c = tw.TwitchClient("cid", "secret", "", "", http=httpx.Client(transport=httpx.MockTransport(handler)))
    cats = c.find_categories("Grand Theft Auto VI")
    s = c.live_streams([cats[0]["id"]])
    assert s[0]["category"] == "Grand Theft Auto VI" and s[0]["source_url"] == "https://www.twitch.tv/streamer"
    assert "640x360" in s[0]["thumbnail_url"]


def test_twitch_eventsub_websocket(monkeypatch):
    """Local WebSocket server speaking the EventSub protocol: welcome -> notification -> raid -> close."""
    from websockets.sync.server import serve

    def ws_handler(conn):
        conn.send(json.dumps({"metadata": {"message_type": "session_welcome"},
                              "payload": {"session": {"id": "sess1", "keepalive_timeout_seconds": 10}}}))
        time.sleep(0.2)
        conn.send(json.dumps({"metadata": {"message_type": "notification", "message_id": "n1",
                                           "message_timestamp": "2026-11-20T10:00:01.123456789Z"},
                              "payload": {"subscription": {"type": "channel.chat.message"},
                                          "event": {"message_id": "chat-1", "chatter_user_id": "42", "message": {"text": "NO WAY"},
                                                    "badges": []}}}))
        conn.send(json.dumps({"metadata": {"message_type": "notification", "message_id": "n2",
                                           "message_timestamp": "2026-11-20T10:00:02Z"},
                              "payload": {"subscription": {"type": "channel.raid"},
                                          "event": {"from_broadcaster_user_name": "Raider", "viewers": 300}}}))
        time.sleep(0.5)

    server = serve(ws_handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tw, "WS_URL", f"ws://127.0.0.1:{port}")
    subs = []
    c = tw.TwitchClient("cid", "", "usertoken", "me")
    monkeypatch.setattr(c, "_subscribe", lambda sid, b: subs.append((sid, b)))
    got = []
    stop = threading.Event()
    for m in c.chat_messages("b1", stop=stop.is_set):
        got.append(m)
        if len(got) == 2:
            stop.set()
            break
    server.shutdown()
    assert subs == [("sess1", "b1")]
    assert got[0].text == "NO WAY" and got[0].user_id == "42" and got[1].kind == "raid"


def test_twitch_chat_requires_user_token():
    with pytest.raises(tw.TwitchError):
        next(tw.TwitchClient("cid", "secret", "", "").chat_messages("b1"))
