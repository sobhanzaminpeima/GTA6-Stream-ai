"""In-process event bus feeding the dashboard's Server-Sent Events stream.

Redis Streams can replace this for multi-process deployments; one creator machine
runs the API and workers in one process, so a thread-safe fan-out is enough.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

_subs: list[queue.Queue] = []
_lock = threading.Lock()
_recent: list[dict[str, Any]] = []


def publish(kind: str, data: dict[str, Any]) -> None:
    msg = {"kind": kind, "at": time.time(), "data": data}
    with _lock:
        _recent.append(msg)
        del _recent[:-200]
        for q in list(_subs):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=1000)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subs:
            _subs.remove(q)


def recent(n: int = 50) -> list[dict[str, Any]]:
    with _lock:
        return list(_recent[-n:])


def sse(msg: dict[str, Any]) -> str:
    return f"event: {msg['kind']}\ndata: {json.dumps(msg, default=str)}\n\n"
