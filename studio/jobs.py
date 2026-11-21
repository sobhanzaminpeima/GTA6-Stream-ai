"""Durable DB-backed job queue + worker threads.

- Jobs survive restarts: a job whose heartbeat is older than STALE_S is re-queued
  (worker crash recovery) and retried up to max_attempts with exponential back-off.
- Cancellation is cooperative: handlers call ctx.check_cancel() between steps.
- Handlers are idempotent per stage (the Shorts pipeline skips stages already done).
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Callable

from sqlalchemy import or_, select, update

from . import events
from .db import session
from .models import Job

HANDLERS: dict[str, Callable] = {}
STALE_S = 120


class Cancelled(Exception):
    pass


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(kind: str, payload: dict, max_attempts: int = 3, delay: float = 0.0) -> int:
    with session() as s:
        j = Job(kind=kind, payload=payload, max_attempts=max_attempts, run_after=time.time() + delay)
        s.add(j)
        s.flush()
        jid = j.id
    events.publish("job", {"id": jid, "kind": kind, "status": "queued"})
    return jid


def cancel(job_id: int) -> None:
    with session() as s:
        j = s.get(Job, job_id)
        if j and j.status in ("queued", "running"):
            j.cancel_requested = True
            if j.status == "queued":
                j.status = "cancelled"


class Ctx:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def progress(self, p: float, msg: str | None = None) -> None:
        with session() as s:
            j = s.get(Job, self.job_id)
            j.progress, j.heartbeat = float(p), time.time()
            if msg:
                j.message = msg
            cancel = j.cancel_requested
        events.publish("job", {"id": self.job_id, "progress": p, "message": msg})
        if cancel:
            raise Cancelled()

    def check_cancel(self) -> None:
        with session() as s:
            if s.get(Job, self.job_id).cancel_requested:
                raise Cancelled()


def _claim(worker: str) -> int | None:
    now = time.time()
    with session() as s:
        # crash recovery: running jobs with a dead heartbeat go back to the queue
        s.execute(update(Job).where(Job.status == "running", Job.heartbeat < now - STALE_S)
                  .values(status="queued", locked_by=None, message="recovered after worker stall"))
        j = s.scalar(select(Job).where(Job.status == "queued", Job.run_after <= now, Job.cancel_requested.is_(False))
                     .order_by(Job.id).limit(1))
        if j is None:
            return None
        res = s.execute(update(Job).where(Job.id == j.id, Job.status == "queued")
                        .values(status="running", locked_by=worker, heartbeat=now, attempts=Job.attempts + 1))
        return j.id if res.rowcount else None


def run_one(job_id: int) -> None:
    with session() as s:
        j = s.get(Job, job_id)
        kind, payload, attempts, maxa = j.kind, dict(j.payload), j.attempts, j.max_attempts
    fn = HANDLERS.get(kind)
    ctx = Ctx(job_id)
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {kind}")
        result = fn(payload, ctx) or {}
        with session() as s:
            j = s.get(Job, job_id)
            j.status, j.progress, j.result, j.error = "done", 1.0, result, None
        events.publish("job", {"id": job_id, "kind": kind, "status": "done"})
    except Cancelled:
        with session() as s:
            s.get(Job, job_id).status = "cancelled"
        events.publish("job", {"id": job_id, "kind": kind, "status": "cancelled"})
    except Exception as e:  # retry with back-off, then fail loudly (never silently)
        tb = traceback.format_exc()[-3000:]
        with session() as s:
            j = s.get(Job, job_id)
            retryable = not getattr(e, "permanent", False) and attempts < maxa
            j.error = f"{type(e).__name__}: {e}\n{tb}"
            j.status = "queued" if retryable else "failed"
            j.run_after = time.time() + 5 * 2 ** attempts
            j.locked_by = None
            from .db import notify
            if not retryable:
                notify(s, f"Job failed: {kind}", f"{type(e).__name__}: {e}"[:500], "error")
        events.publish("job", {"id": job_id, "kind": kind, "status": "queued" if retryable else "failed", "error": str(e)[:300]})


class WorkerPool:
    def __init__(self, n: int = 2):
        self.n = n
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self.n):
            t = threading.Thread(target=self._loop, args=(f"{os.getpid()}-w{i}",), daemon=True, name=f"worker-{i}")
            t.start()
            self.threads.append(t)

    def _loop(self, name: str) -> None:
        while not self._stop.is_set():
            try:
                jid = _claim(name)
            except Exception:
                jid = None
            if jid is None:
                self._stop.wait(0.5)
                continue
            run_one(jid)

    def stop(self) -> None:
        self._stop.set()
        for t in self.threads:
            t.join(5)


def drain(timeout: float = 600) -> None:
    """Run queued jobs inline until the queue is empty (tests / CLI demo)."""
    end = time.time() + timeout
    while time.time() < end:
        jid = _claim("inline")
        if jid is None:
            with session() as s:
                pending = s.scalar(select(Job.id).where(or_(Job.status == "running", (Job.status == "queued") & (Job.cancel_requested.is_(False)))).limit(1))
                if pending is None:
                    return
                nxt = s.scalar(select(Job.run_after).where(Job.status == "queued").order_by(Job.run_after).limit(1))
            if nxt and nxt > time.time():
                time.sleep(min(nxt - time.time(), 2))
            else:
                time.sleep(0.2)
            continue
        run_one(jid)
