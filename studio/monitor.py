"""Live monitoring orchestrator: chat -> detector -> candidates, plus the authorized buffer.

One MonitorRuntime per MonitoringSession, each in its own thread. Chat and media are
independent: chat runs for any stream with public chat; the buffer starts only when
rights.require_capture() passes AND the owner turned recording on.
"""
from __future__ import annotations

import pathlib
import queue
import threading
import time
import traceback
from collections import Counter

from sqlalchemy import select

from . import events, rights, scoring
from .chat import RawMessage
from .chat.normalize import Normalizer, hash_user
from .db import audit, automation, notify, session
from .detection import Moment, SpikeDetector
from .media import RollingBuffer, SegmentInfo, audio_excitement, audio_features, decode_audio, visual_features, visual_importance
from .models import (ChatEvent, ChatWindow, DetectedSpike, Livestream, MediaSegment, MonitoringSession, MonitorStatus,
                     RightsState, Streamer, TimestampMapping, ViralCandidate, now)

RUNTIMES: dict[int, "MonitorRuntime"] = {}


def _set_status(session_id: int, status: str, detail: str | None = None) -> None:
    with session() as s:
        ms = s.get(MonitoringSession, session_id)
        if ms:
            ms.status, ms.status_detail, ms.heartbeat = status, detail, now()
    events.publish("status", {"session_id": session_id, "status": status, "detail": detail})


class MonitorRuntime:
    def __init__(self, session_id: int, chat_iter_factory, buffer_source: str | None, settings_snapshot: dict,
                 custom_keywords: dict | None = None, lag_s: int = 1):
        self.session_id = session_id
        # polled chat (YouTube) delivers messages in batches: evaluate seconds only once
        # late messages for them have had time to arrive
        self.lag_s = lag_s
        self.chat_iter_factory = chat_iter_factory
        self.cfg = settings_snapshot
        self.normalizer = Normalizer(custom_keywords, settings_snapshot.get("keyword_weights"))
        self.det = SpikeDetector(sensitivity=float(settings_snapshot["spike_sensitivity"]),
                                 min_messages=int(settings_snapshot["min_messages_per_window"]),
                                 merge_gap_s=int(settings_snapshot["merge_gap_s"]),
                                 max_per_hour=int(settings_snapshot["max_candidates_per_hour"]))
        self.buffer: RollingBuffer | None = None
        self.buffer_source = buffer_source
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.pending: list[tuple] = []
        self.moment_cand: dict[int, int] = {}  # id(moment) -> candidate id
        self.last_window_written: int | None = None
        self.audio_baseline: list[float] = []
        self.lock = threading.Lock()
        self.chat_done = False
        self.chat_error: tuple[str, str] | None = None
        self.analysis_q: queue.Queue = queue.Queue()
        self.analysis_thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.buffer_source:
            self.buffer = RollingBuffer(self.session_id, self.buffer_source, on_segment=self._on_segment,
                                        on_status=lambda st, d: _set_status(self.session_id, st if st != "STOPPED" else "MONITORING", d))
            self.buffer.start()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"monitor-{self.session_id}")
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.buffer:
            self.buffer.stop()
        if self.thread and threading.current_thread() is not self.thread:
            self.thread.join(15)

    # ---------------------------------------------------------------- chat loop
    def _run(self) -> None:
        _set_status(self.session_id, MonitorStatus.MONITORING.value if not self.buffer else MonitorStatus.BUFFERING.value,
                    "chat connected")
        feeder = threading.Thread(target=self._feed, daemon=True, name=f"chat-{self.session_id}")
        feeder.start()
        last_retention = 0.0
        try:
            while not self._stop.is_set():
                self._flush(int(time.time()) - self.lag_s)
                if time.time() - last_retention > 5:
                    self._retention()
                    self._check_post_roll()
                    last_retention = time.time()
                    with session() as s:
                        ms = s.get(MonitoringSession, self.session_id)
                        ms.heartbeat = now()
                if self.chat_done and (not self.buffer or self.buffer.ended or not self.buffer.thread.is_alive()):
                    self._flush(int(time.time()) + self.det.merge_gap_s + 2)
                    self._check_post_roll()
                    break
                time.sleep(0.25)
        except Exception as e:
            _set_status(self.session_id, MonitorStatus.ERROR.value, f"{type(e).__name__}: {e}")
            traceback.print_exc()
            return
        finally:
            self._flush(int(time.time()))
        if not self._stop.is_set():
            # stream ended naturally: keep the buffer until queued clip jobs pinned what they need
            with session() as s:  # never hide a chat failure behind "stream ended"
                ms = s.get(MonitoringSession, self.session_id)
                st, detail = self.chat_error or (MonitorStatus.STOPPED.value, "stream ended")
                ms.status, ms.ended_at, ms.status_detail = st, now(), detail
            events.publish("status", {"session_id": self.session_id, "status": st})

    def _feed(self) -> None:
        try:
            for raw in self.chat_iter_factory(lambda: self._stop.is_set(),
                                              lambda st, d: _set_status(self.session_id, st, d)):
                self.ingest(raw)
                if self._stop.is_set():
                    break
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            st = MonitorStatus.CHAT_UNAVAILABLE.value if any(k in msg for k in ("liveChat", "chat", "Chat", "not configured", "notConfigured")) else MonitorStatus.ERROR.value
            self.chat_error = (st, msg[:300])
            _set_status(self.session_id, st, msg[:300])
            with session() as s:
                audit(s, "chat_feed_error", f"session:{self.session_id}", severity="warn", error=msg[:300])
        finally:
            self.chat_done = True

    def ingest(self, raw: RawMessage) -> None:
        uh = hash_user(raw.platform, raw.user_id)
        n = self.normalizer.process(raw.text, uh, raw.ts, raw.is_bot, raw.kind if raw.kind != "message" else None)
        with self.lock:
            self.det.add(raw.ts, uh, n.reaction_weight, n.emoji_count, n.is_spam, n.kind, n.reactions, n.sentiment, n.normalized)
            self.pending.append((raw, uh, n))

    # ---------------------------------------------------------------- per-second processing
    def _flush(self, upto_second: int) -> None:
        with self.lock:
            pending, self.pending = self.pending, []
            fired = self.det.tick(upto_second)
            timeline = [t for t in self.det.timeline if self.last_window_written is None or t["second"] > self.last_window_written]
            moments = list(self.det.moments)
        if not pending and not fired and not timeline:
            return
        with session() as s:
            for raw, uh, n in pending:
                s.add(ChatEvent(session_id=self.session_id, platform=raw.platform, platform_msg_id=raw.msg_id, ts=raw.ts,
                                content=raw.text[:500], normalized=n.normalized[:500], user_hash=uh, emoji_count=n.emoji_count,
                                sentiment=n.sentiment, keywords=n.keywords, reaction_weight=n.reaction_weight,
                                is_spam=n.is_spam, spam_reason=n.spam_reason, kind=n.kind))
            for t in timeline:
                s.add(ChatWindow(session_id=self.session_id, second=t["second"], messages=t["messages"],
                                 unique_users=t["unique_users"], reaction=t["reaction"], emojis=t["emojis"],
                                 spam=t["spam"], score=t["score"]))
            if timeline:
                self.last_window_written = timeline[-1]["second"]
            ms = s.get(MonitoringSession, self.session_id)
            ms.messages_seen += len(pending)
            spike_rows = []
            for sp in fired:
                row = DetectedSpike(session_id=self.session_id, ts=float(sp.second), anomaly_score=sp.score, metrics=sp.metrics,
                                    dominant_reaction=sp.dominant, suppressed=sp.suppressed, suppress_reason=sp.suppress_reason,
                                    explanation=sp.explanation)
                s.add(row)
                spike_rows.append((sp, row))
            s.flush()
            for m in moments:
                self._upsert_candidate(s, m)
            for sp, row in spike_rows:
                for m in moments:
                    if sp in m.spikes and id(m) in self.moment_cand:
                        row.candidate_id = self.moment_cand[id(m)]
        if timeline:
            events.publish("chat_tick", {"session_id": self.session_id, "points": timeline[-30:]})
        for sp in fired:
            events.publish("spike", {"session_id": self.session_id, "second": sp.second, "score": round(sp.score, 2),
                                     "suppressed": sp.suppressed, "reason": sp.suppress_reason, "explanation": sp.explanation})

    def _upsert_candidate(self, s, m: Moment) -> None:
        ms = s.get(MonitoringSession, self.session_id)
        ls = s.get(Livestream, ms.livestream_id)
        a = automation(s)
        cid = self.moment_cand.get(id(m))
        cand = s.get(ViralCandidate, cid) if cid else None
        created = cand is None
        if created:
            key = f"{self.session_id}:{m.first_second}"
            cand = s.scalar(select(ViralCandidate).where(ViralCandidate.dedupe_key == key))
            if cand is None:
                cand = ViralCandidate(session_id=self.session_id, livestream_id=ls.id, dedupe_key=key,
                                      event_ts=float(m.onset_second), first_spike_ts=float(m.first_second),
                                      last_spike_ts=float(m.last_second), source_url=ls.source_url)
                s.add(cand)
                s.flush()
            self.moment_cand[id(m)] = cand.id
        elif cand.spike_count == len(m.spikes) and cand.last_spike_ts == m.last_second:
            return
        cand.last_spike_ts, cand.spike_count = float(m.last_second), len(m.spikes)
        latency = self._latency(s, a)
        event_wall = scoring.estimate_event_wall(m.onset_second, latency)
        cand.event_ts = event_wall
        maps = [(t.wall_ts, t.media_ts, t.uncertainty_s) for t in
                s.scalars(select(TimestampMapping).where(TimestampMapping.session_id == self.session_id))]
        mt = scoring.map_to_media(event_wall, maps)
        cand.media_ts, cand.media_ts_uncertainty = mt.media_ts, mt.uncertainty_s
        if ls.started_at and ls.platform == "youtube" and not ls.is_simulation:
            rel = max(0, int(event_wall - ls.started_at.timestamp() - 15))
            cand.source_timestamp_url = f"{ls.source_url}&t={rel}s"
        dec = rights.decide(s, ls)
        cand.rights_state = dec.state
        if self.buffer is None:
            cand.clip_readiness = "report_only"
        elif cand.clip_readiness in ("report_only", "no_media", "awaiting_post_roll"):
            cand.clip_readiness = "awaiting_post_roll" if mt.media_ts is not None else "no_media"
        dominant = m.dominant
        cand.dominant_reaction = dominant
        pm = m.peak_metrics
        cand.chat_score = scoring.chat_component(m.peak_score, len(m.spikes), pm.get("unique_users_10s", 0))
        reactions = dict(m.reactions)
        dur = m.last_second - m.onset_second + 10
        narrative, n_why = scoring.narrative_component(reactions, dur, m.onset_second < m.first_second - 2)
        similar = s.scalar(select(ViralCandidate.id).where(ViralCandidate.dominant_reaction == dominant,
                                                           ViralCandidate.production_status.in_(["in_production", "ready_for_approval", "approved", "published"]))
                           .limit(5)) is not None
        orig, o_why = scoring.originality_component(1 if similar else 0, dec.state == RightsState.OWNED.value)
        cand.narrative_score, cand.originality_score = narrative, orig
        res = scoring.combine({"chat": cand.chat_score, "audio": cand.audio_score, "visual": cand.visual_score,
                               "narrative": narrative, "originality": orig}, a["score_weights"],
                              {"chat": f"peak z {m.peak_score:.1f}, {len(m.spikes)} spike(s)", "narrative": n_why + " (heuristic)",
                               "originality": o_why})
        cand.overall_score, cand.confidence, cand.tier = res.overall, res.confidence, res.tier
        top = ", ".join(f"{k}" for k, _ in Counter(reactions).most_common(3))
        cand.description = (f"Chat spike: {pm.get('rate_10s', 0):.0f} msg/s vs {pm.get('baseline_rate', 0):.1f}/s baseline, "
                            f"{pm.get('unique_users_10s', 0)} users reacting ({top}).")
        cand.explanation = {"score": res.explanation, "weights": res.weights_used, "peak_metrics": pm,
                            "spikes": [sp.explanation for sp in m.spikes[-5:]],
                            "timing": {"onset_wall": m.onset_second, "chat_latency_s": latency, "event_wall": event_wall,
                                       "media_ts": mt.media_ts, "uncertainty_s": mt.uncertainty_s, "method": mt.method},
                            "rights": dec.as_dict()}
        if created:
            audit(s, "candidate_created", f"candidate:{cand.id}", score=cand.overall_score, rights=dec.state)
            if a.get("notifications") and cand.overall_score >= a["min_candidate_score"]:
                notify(s, f"Viral moment: {ls.channel_name}", cand.description, "info", f"#moments/{cand.id}")
            events.publish("candidate", {"id": cand.id, "created": True, "score": cand.overall_score})
        else:
            events.publish("candidate", {"id": cand.id, "created": False, "score": cand.overall_score})

    def _latency(self, s, a) -> float:
        samples = [t.chat_latency_s for t in s.scalars(select(TimestampMapping).where(TimestampMapping.source == "manual_calibration"))]
        return scoring.calibrated_latency(samples, float(a["chat_latency_s"]))

    # ---------------------------------------------------------------- media side
    def _on_segment(self, info: SegmentInfo) -> None:
        """Register the segment + timeline anchor at once; signal analysis runs on its own
        thread so a busy CPU never delays the timeline or post-roll checks."""
        with session() as s:
            if s.scalar(select(MediaSegment.id).where(MediaSegment.session_id == self.session_id, MediaSegment.seq == info.seq)):
                return
            seg = MediaSegment(session_id=self.session_id, seq=info.seq, path=info.path, media_start=info.media_start,
                               duration=info.duration, wall_start=info.wall_start, bytes=info.bytes, analysis={})
            s.add(seg)
            s.add(TimestampMapping(session_id=self.session_id, wall_ts=info.wall_start, media_ts=info.media_start,
                                   source="segment", uncertainty_s=0.6))
            s.flush()
            seg_id = seg.id
        self.analysis_q.put((seg_id, info))
        if self.analysis_thread is None or not self.analysis_thread.is_alive():
            self.analysis_thread = threading.Thread(target=self._analyse_loop, daemon=True, name=f"signals-{self.session_id}")
            self.analysis_thread.start()

    def _analyse_loop(self) -> None:
        while True:
            try:
                seg_id, info = self.analysis_q.get(timeout=30)
            except queue.Empty:
                return
            if self.analysis_q.qsize() > 30:  # far behind: skip to stay near real time (scores stay empty)
                continue
            try:
                feat_a = audio_features(decode_audio(info.path))
                if feat_a.get("frames"):
                    self.audio_baseline.append(feat_a["median_db"])
                recent = self.audio_baseline[-150:]
                base = sorted(recent)[len(recent) // 2] if len(recent) > 5 else None
                a_score, a_tags = audio_excitement(feat_a, base)
                feat_v = visual_features(info.path, fps=2.0)
                v_score, v_tags = visual_importance(feat_v)
            except Exception:  # segment deleted by retention meanwhile
                continue
            with session() as s:
                seg = s.get(MediaSegment, seg_id)
                if seg:
                    seg.audio_rms, seg.audio_score, seg.visual_score = feat_a.get("median_db"), a_score, v_score
                    seg.analysis = {"audio": a_tags, "visual": v_tags, "cuts": feat_v.get("cuts", [])}
            events.publish("segment", {"session_id": self.session_id, "seq": info.seq, "media_start": info.media_start,
                                       "audio": a_score, "visual": v_score})

    def _retention(self) -> None:
        if not self.buffer:
            return
        keep_s = float(self.cfg.get("buffer_minutes", 10)) * 60
        cutoff = time.time() - keep_s
        with session() as s:
            old = s.scalars(select(MediaSegment).where(MediaSegment.session_id == self.session_id, MediaSegment.deleted.is_(False),
                                                       MediaSegment.wall_start + MediaSegment.duration < cutoff)).all()
            for seg in old:
                if seg.pinned_by:
                    continue  # an active clip job still needs it
                pathlib.Path(seg.path).unlink(missing_ok=True)
                seg.deleted = True

    def _check_post_roll(self) -> None:
        if not self.buffer:
            return
        to_enqueue: list[dict] = []
        self._post_roll_scan(to_enqueue)
        from .jobs import enqueue  # after our transaction committed (SQLite: one writer at a time)
        for payload in to_enqueue:
            enqueue("extract_clip", payload)

    def _post_roll_scan(self, to_enqueue: list[dict]) -> None:
        with session() as s:
            a = automation(s)
            post = float(a["post_roll_s"])
            cands = s.scalars(select(ViralCandidate).where(ViralCandidate.session_id == self.session_id,
                                                           ViralCandidate.clip_readiness == "awaiting_post_roll")).all()
            if not cands:
                return
            segs = s.scalars(select(MediaSegment).where(MediaSegment.session_id == self.session_id, MediaSegment.deleted.is_(False))).all()
            if not segs:
                return
            covered_to = max(x.media_start + x.duration for x in segs)
            for c in cands:
                if c.media_ts is None:
                    continue
                # wait for the post-roll (plus room for the longest cut), unless the stream ended
                need = c.media_ts + max(post, 35)
                if covered_to >= need or (self.buffer.ended and covered_to > c.media_ts):
                    pre = float(a["pre_roll_s"])
                    lo, hi = c.media_ts - pre - 12, c.media_ts + max(post, 35) + 2
                    pin_ids = []
                    for x in segs:
                        if x.media_start + x.duration > lo and x.media_start < hi:
                            x.pinned_by = list(set((x.pinned_by or []) + [f"cand{c.id}"]))
                            pin_ids.append(x.id)
                    c.clip_readiness = "extracting"
                    to_enqueue.append({"candidate_id": c.id, "segment_ids": pin_ids})


# ---------------------------------------------------------------- public API
def chat_source_for(ls: Livestream, sim=None):
    """Returns a factory(stop, on_status) -> iterator of RawMessage."""
    if ls.is_simulation:
        return lambda stop, on_status: sim.stream(sim_duration(sim), stop)
    if ls.platform == "youtube":
        from .chat.youtube import YouTubeClient
        from .publishing import youtube_oauth_token

        def f(stop, on_status):
            def usage(op, units):
                from .costs import record
                with session() as s:
                    record(s, "youtube_data", op, units)
            yt = YouTubeClient(oauth_token=youtube_oauth_token(), usage_cb=usage)
            chat_id = ls.live_chat_id
            if not chat_id:
                det = yt.video_details([ls.stream_id])
                chat_id = det[0]["live_chat_id"] if det else None
            if not chat_id:
                raise RuntimeError("liveChat unavailable: the broadcast has no active live chat")
            return yt.poll_chat(chat_id, stop=stop)
        return f
    if ls.platform == "twitch":
        from .chat.twitch import TwitchClient
        return lambda stop, on_status: TwitchClient().chat_messages(ls.channel_id, stop, on_status)
    raise ValueError(f"unsupported platform {ls.platform}")


def sim_duration(sim) -> float:
    return max([sc.at + sc.duration for sc in sim.scenarios] + [60]) + 40


def start_monitoring(livestream_id: int, record_media: bool = False, sim=None, actor: str = "owner") -> int:
    with session() as s:
        ls = s.get(Livestream, livestream_id)
        if ls is None:
            raise ValueError("livestream not found")
        for sid, rt in list(RUNTIMES.items()):
            ms = s.get(MonitoringSession, sid)
            if ms and ms.livestream_id == livestream_id and rt.thread and rt.thread.is_alive():
                return sid
        a = automation(s)
        ms = MonitoringSession(livestream_id=ls.id, buffer_minutes=float(a["buffer_minutes"]), status=MonitorStatus.CONNECTED.value)
        s.add(ms)
        s.flush()
        sid = ms.id
        buffer_source = None
        if record_media:
            if not a.get("recording_enabled"):
                ms.status_detail = "recording disabled in Automation settings"
            else:
                try:
                    rights.require_capture(s, ls, actor)
                    if not ls.media_source:
                        ms.status_detail = "authorized, but no ingest source configured for this stream"
                    else:
                        buffer_source = ls.media_source
                        ms.buffer_enabled = True
                        audit(s, "buffer_started", f"livestream:{ls.id}", actor, source=ls.media_source)
                except rights.RightsError as e:
                    ms.status, ms.status_detail = MonitorStatus.SOURCE_RESTRICTED.value, str(e)
        if ls.streamer_id:
            st = s.get(Streamer, ls.streamer_id)
            st.last_monitored_stream_id = ls.id
            custom = {k: 1.5 for k in (st.custom_keywords or [])}
        else:
            custom = {}
        audit(s, "monitoring_started", f"livestream:{ls.id}", actor, session=sid, buffer=bool(buffer_source))
        factory = chat_source_for(ls, sim)
        lag_s = 1 if ls.is_simulation else (8 if ls.platform == "youtube" else 2)
        snapshot = dict(a)
        restricted = ms.status == MonitorStatus.SOURCE_RESTRICTED.value
    rt = MonitorRuntime(sid, factory, buffer_source, snapshot, custom, lag_s)
    RUNTIMES[sid] = rt
    rt.start()
    if restricted:  # chat still runs; keep the restriction visible
        time.sleep(0.3)
        with session() as s:
            ms = s.get(MonitoringSession, sid)
            ms.status_detail = "media restricted - chat only. " + (ms.status_detail or "")
    return sid


def stop_monitoring(session_id: int) -> None:
    rt = RUNTIMES.pop(session_id, None)
    if rt:
        rt.stop()
    with session() as s:
        ms = s.get(MonitoringSession, session_id)
        if ms and ms.status != MonitorStatus.STOPPED.value:
            ms.status, ms.ended_at = MonitorStatus.STOPPED.value, now()


def calibrate(candidate_id: int, true_media_ts: float) -> float:
    """Owner marks where the moment really happened; we store the implied chat latency."""
    with session() as s:
        c = s.get(ViralCandidate, candidate_id)
        maps = [(t.wall_ts, t.media_ts) for t in s.scalars(select(TimestampMapping).where(
            TimestampMapping.session_id == c.session_id, TimestampMapping.source == "segment"))]
        if not maps:
            raise ValueError("no media timeline for this candidate")
        w, m0 = min(maps, key=lambda x: abs(x[1] - true_media_ts))
        true_wall = w + (true_media_ts - m0)
        onset = c.explanation.get("timing", {}).get("onset_wall", c.first_spike_ts)
        lat = float(onset) - true_wall
        s.add(TimestampMapping(session_id=c.session_id, wall_ts=true_wall, media_ts=true_media_ts,
                               source="manual_calibration", chat_latency_s=lat, uncertainty_s=0.5))
        c.media_ts, c.media_ts_uncertainty = true_media_ts, 0.5
        audit(s, "timing_calibrated", f"candidate:{c.id}", "owner", latency=lat)
        return lat
