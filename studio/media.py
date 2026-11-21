"""Authorized rolling media buffer, clip extraction and cheap audio/visual signals.

Only started through `RollingBuffer.start()`, which calls rights.require_capture().
The ingest source must be a URL or path the owner configured for that stream
(their own OBS/RTMP/SRT/HLS output, a licensor-supplied feed, or a local recording).
The app never resolves or scrapes media from a public watch page.

Layout: studio_data/buffers/session_<id>/seg_000123.ts + segments.csv (FFmpeg segment
muxer). Every finished segment is registered with wall-clock and media-time anchors;
the retention manager deletes expired segments unless a clip job pinned them.
"""
from __future__ import annotations

import csv
import json
import pathlib
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import numpy as np

from .config import settings


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffprobe_exe() -> str | None:
    return shutil.which("ffprobe")


FF = ffmpeg_exe()
NOWIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def run(args: list[str], timeout: float | None = 600) -> subprocess.CompletedProcess:
    p = subprocess.run([FF, "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({p.returncode}): {p.stderr[-1500:]}")
    return p


def _frac(s: str) -> float:
    try:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | pathlib.Path) -> dict:
    """Duration / streams via ffprobe when available, else parse ffmpeg -i."""
    path = str(path)
    fp = ffprobe_exe()
    if fp:
        p = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, text=True, creationflags=NOWIN)
        if p.returncode == 0:
            d = json.loads(p.stdout)
            v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
            a = next((s for s in d["streams"] if s["codec_type"] == "audio"), {})
            return {"duration": float(d["format"].get("duration", 0)), "width": v.get("width"), "height": v.get("height"),
                    "vcodec": v.get("codec_name"), "acodec": a.get("codec_name"),
                    "fps": _frac(v.get("r_frame_rate", "0/1")),
                    "sample_rate": int(a.get("sample_rate", 0) or 0)}
    import re
    p = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NOWIN)
    err = p.stderr
    out = {"duration": 0.0, "width": None, "height": None, "vcodec": None, "acodec": None, "fps": 0, "sample_rate": 0}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        out["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        out.update(vcodec=m[1], width=int(m[2]), height=int(m[3]))
    m = re.search(r"([\d.]+) fps", err)
    if m:
        out["fps"] = float(m[1])
    m = re.search(r"Audio: (\w+)[^,\n]*, (\d+) Hz", err)
    if m:
        out.update(acodec=m[1], sample_rate=int(m[2]))
    return out


# ---------------------------------------------------------------- rolling buffer
@dataclass
class SegmentInfo:
    seq: int
    path: str
    media_start: float
    duration: float
    wall_start: float
    bytes: int


class RollingBuffer:
    """FFmpeg ingest -> 2 s MPEG-TS segments, registered as they complete."""

    def __init__(self, session_id: int, source: str, seg_seconds: float = 2.0, realtime: bool = True,
                 on_segment=None, on_status=None, transcode: bool = True):
        self.session_id = session_id
        self.source = source
        self.seg_seconds = seg_seconds
        self.realtime = realtime
        self.on_segment = on_segment
        self.on_status = on_status
        self.transcode = transcode
        self.dir = settings.data_dir / "buffers" / f"session_{session_id}"
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.seen: set[str] = set()
        self.media_offset = 0.0  # media time of segment 0 after a restart (interruption recovery)
        self.restarts = 0
        self.started_wall: float | None = None
        self.ended = False

    def _cmd(self, start_number: int) -> list[str]:
        src = []
        if self.realtime and not self.source.startswith(("rtmp://", "srt://", "udp://")) and pathlib.Path(self.source).exists():
            src += ["-re"]  # local file simulating a live feed
        if self.source.startswith(("http://", "https://")):
            src += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
        src += ["-i", self.source]
        if self.media_offset and pathlib.Path(self.source).exists():
            src = ["-ss", f"{self.media_offset:.2f}"] + src  # resume a file feed where we stopped
        codec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                  "-force_key_frames", f"expr:gte(t,n_forced*{self.seg_seconds})", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
                 if self.transcode else ["-c", "copy"])
        return ["-hide_banner", "-nostdin", "-loglevel", "error", *src, *codec, "-f", "segment",
                "-segment_time", str(self.seg_seconds), "-segment_time_delta", "0.1", "-segment_format", "mpegts", "-reset_timestamps", "1",
                "-segment_start_number", str(start_number), "-segment_list", str(self.dir / "segments.csv"),
                "-segment_list_type", "csv", "-segment_list_flags", "+live", str(self.dir / "seg_%06d.ts")]

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"buffer-{self.session_id}")
        self.thread.start()

    def _loop(self) -> None:
        backoff = 1.0
        next_seq = 0
        while not self._stop.is_set():
            self.started_wall = time.time()
            self.proc = subprocess.Popen([FF, "-y", *self._cmd(next_seq)], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, creationflags=NOWIN)
            if self.on_status:
                self.on_status("BUFFERING", f"ingest running (restart {self.restarts})")
            while self.proc.poll() is None and not self._stop.is_set():
                self._scan()
                time.sleep(0.25)
            self._scan()
            if self._stop.is_set():
                break
            rc = self.proc.returncode
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")[-400:] if self.proc.stderr else ""
            if rc == 0 and pathlib.Path(self.source).exists():
                self.ended = True  # file feed finished = stream ended
                if self.on_status:
                    self.on_status("STOPPED", "source ended")
                break
            # live feed dropped: reconnect and continue numbering (no overwrite of pinned segments)
            self.restarts += 1
            next_seq = max([int(pathlib.Path(p).stem.split("_")[1]) for p in self.seen] or [-1]) + 1
            if self.on_status:
                self.on_status("RECONNECTING", f"ingest exited rc={rc}: {err.strip()[:160]}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _scan(self) -> None:
        lst = self.dir / "segments.csv"
        if not lst.exists():
            return
        try:
            rows = list(csv.reader(lst.read_text(encoding="utf-8").splitlines()))
        except OSError:
            return
        for row in rows:
            if len(row) < 3 or row[0] in self.seen:
                continue
            name, start, end = row[0], float(row[1]), float(row[2])
            p = self.dir / name
            if not p.exists():
                continue
            self.seen.add(name)
            seq = int(pathlib.Path(name).stem.split("_")[1])
            dur = max(end - start, 0.01)
            # with -reset_timestamps the csv start is the running media position of this ingest run
            media_start = self.media_offset_for(start)
            # the file's last write = when the segment closed (live-paced), independent of how
            # late we scan it; it began ~dur before that
            wall_start = p.stat().st_mtime - dur
            info = SegmentInfo(seq, str(p), media_start, dur, wall_start, p.stat().st_size)
            if self.on_segment:
                self.on_segment(info)

    def media_offset_for(self, start: float) -> float:
        return self.media_offset + start

    def stop(self) -> None:
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.thread:
            self.thread.join(10)


def extract_clip(segments: list[dict], t0: float, t1: float, out: pathlib.Path) -> dict:
    """segments: [{path, media_start, duration}] sorted. Cuts [t0, t1] (media time) to MP4,
    re-encoding so the cut is frame-accurate and audio stays in sync."""
    segs = [s for s in segments if s["media_start"] + s["duration"] > t0 and s["media_start"] < t1]
    if not segs:
        raise ValueError("no buffered segments cover the requested range")
    segs.sort(key=lambda s: s["media_start"])
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{pathlib.Path(s['path']).as_posix()}'\n" for s in segs), encoding="utf-8")
    ss = max(0.0, t0 - segs[0]["media_start"])
    dur = min(t1, segs[-1]["media_start"] + segs[-1]["duration"]) - max(t0, segs[0]["media_start"])
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", str(out)])
    lst.unlink(missing_ok=True)
    return {"path": str(out), "media_in": max(t0, segs[0]["media_start"]), "media_out": max(t0, segs[0]["media_start"]) + dur,
            "duration": dur, "segments": [s["path"] for s in segs]}


# ---------------------------------------------------------------- signals
def decode_audio(path: str, sr: int = 16000, ss: float | None = None, t: float | None = None) -> np.ndarray:
    args = [FF, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if ss is not None:
        args += ["-ss", f"{ss:.3f}"]
    args += ["-i", path]
    if t is not None:
        args += ["-t", f"{t:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    p = subprocess.run(args, capture_output=True, creationflags=NOWIN)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _movavg(x: np.ndarray, w: int) -> np.ndarray:
    """Centered moving average, same length as x, edge-padded (window capped to the signal)."""
    w = max(1, min(int(w), len(x)))
    pad = np.pad(x, (w // 2, w - 1 - w // 2), mode="edge")
    return np.convolve(pad, np.ones(w) / w, mode="valid")


def audio_features(x: np.ndarray, sr: int = 16000, frame_s: float = 0.05) -> dict:
    """RMS energy, loudness jumps, voice-activity estimate, dramatic silences. Heuristic,
    not a trained classifier: labels are 'likely', never certain."""
    n = int(sr * frame_s)
    if len(x) < n * 4:
        return {"frames": 0, "rms_db": [], "voice_ratio": 0.0, "peak_jump_db": 0.0, "silences": 0, "loud_ratio": 0.0}
    k = len(x) // n
    fr = x[: k * n].reshape(k, n)
    rms = np.sqrt((fr ** 2).mean(1) + 1e-10)
    db = 20 * np.log10(rms + 1e-9)
    zcr = (np.abs(np.diff(np.sign(fr), axis=1)) > 0).mean(1)
    # speech: mid energy + moderate zero-crossing rate; music/explosions tend to be extreme
    noise_floor = np.percentile(db, 10)
    voiced = (db > noise_floor + 12) & (zcr > 0.02) & (zcr < 0.25)
    med = np.median(db)
    win = max(1, int(1.0 / frame_s))
    smooth = _movavg(db, win)
    base = _movavg(db, win * 8)
    jumps = smooth - base
    silent = db < med - 18
    sil_runs, run_len = 0, 0
    for s_ in silent:
        run_len = run_len + 1 if s_ else 0
        if run_len == int(0.8 / frame_s):
            sil_runs += 1
    return {"frames": int(k), "rms_db": db.round(1).tolist(), "median_db": float(med), "voice_ratio": float(voiced.mean()),
            "peak_jump_db": float(jumps.max()), "loud_ratio": float((db > med + 6).mean()), "silences": int(sil_runs),
            "peak_db": float(db.max())}


def audio_excitement(feat: dict, baseline_db: float | None = None) -> tuple[float, list[str]]:
    if not feat.get("frames"):
        return 0.0, ["no audio"]
    tags, s = [], 0.0
    jump = feat["peak_jump_db"]
    s += min(45, max(0, jump - 3) * 6)
    if jump > 8:
        tags.append("sudden volume increase")
    if baseline_db is not None and feat["median_db"] > baseline_db + 4:
        s += min(20, (feat["median_db"] - baseline_db) * 3)
        tags.append("louder than the stream's baseline")
    if feat["voice_ratio"] > 0.35 and feat["loud_ratio"] > 0.15:
        s += 20
        tags.append("likely excited speech")
    if feat["silences"]:
        s += 8
        tags.append("dramatic pause")
    if feat["voice_ratio"] < 0.1 and feat["loud_ratio"] > 0.3:
        s *= 0.6  # loud but not speech: probably gameplay audio, not a reaction
        tags.append("loud non-speech (gameplay audio?)")
    return round(min(100.0, s), 1), tags or ["steady audio"]


def visual_features(path: str, ss: float | None = None, t: float | None = None, fps: float = 4.0) -> dict:
    """Low-res grayscale sampling: motion energy, scene cuts, histogram novelty."""
    w, h = 160, 90
    args = [FF, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if ss is not None:
        args += ["-ss", f"{ss:.3f}"]
    args += ["-i", path]
    if t is not None:
        args += ["-t", f"{t:.3f}"]
    args += ["-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.run(args, capture_output=True, creationflags=NOWIN)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    if n < 2:
        return {"frames": n, "motion": [], "cuts": [], "novelty": 0.0}
    fr = buf[: n * w * h].reshape(n, h, w).astype(np.float32)
    diff = np.abs(np.diff(fr, axis=0)).mean((1, 2))
    hists = np.stack([np.histogram(f, bins=32, range=(0, 255))[0] / (w * h) for f in fr])
    hd = np.abs(np.diff(hists, axis=0)).sum(1)
    cuts = [round((i + 1) / fps, 2) for i, (d, hh) in enumerate(zip(diff, hd)) if d > 30 or hh > 0.6]
    novelty = float(np.abs(hists - hists.mean(0)).sum(1).max())
    return {"frames": int(n), "motion": diff.round(2).tolist(), "mean_motion": float(diff.mean()),
            "peak_motion": float(diff.max()), "cuts": cuts, "novelty": round(novelty, 3),
            "mean_luma": float(fr.mean())}


def visual_importance(feat: dict) -> tuple[float, list[str]]:
    if feat.get("frames", 0) < 2:
        return 0.0, ["no frames"]
    tags, s = [], 0.0
    s += min(40, feat["mean_motion"] * 2.2)
    if feat["mean_motion"] > 10:
        tags.append("high motion")
    s += min(25, len(feat["cuts"]) * 6)
    if feat["cuts"]:
        tags.append(f"{len(feat['cuts'])} scene change(s)")
    s += min(25, feat["novelty"] * 40)
    if feat["novelty"] > 0.4:
        tags.append("visually novel section")
    if feat.get("mean_luma", 100) < 12:
        s *= 0.3
        tags.append("mostly black frames")
    return round(min(100.0, s), 1), tags or ["static image"]


def grab_frame(path: str, at: float, out: pathlib.Path, width: int = 1280) -> pathlib.Path:
    run(["-ss", f"{max(at, 0):.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)], timeout=60)
    return out


def scene_boundaries(path: str, threshold: float = 0.35) -> list[float]:
    p = subprocess.run([FF, "-hide_banner", "-nostdin", "-i", path, "-vf", f"select='gt(scene,{threshold})',showinfo",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NOWIN)
    import re
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def loudness(path: str) -> float | None:
    p = subprocess.run([FF, "-hide_banner", "-nostdin", "-i", path, "-af", "ebur128=peak=none", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)
    import re
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else None


def black_frames(path: str) -> list[tuple[float, float]]:
    p = subprocess.run([FF, "-hide_banner", "-nostdin", "-i", path, "-vf", "blackdetect=d=0.4:pix_th=0.06", "-an",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NOWIN)
    import re
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", p.stderr)]
