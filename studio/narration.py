"""Narration: one audio file per sentence (so a single line can be regenerated),
with word timings.

Providers:
- elevenlabs: channel narrator "Brian" with character timestamps (exact timing). Paid:
  passes costs.check_budget first.
- windows_sapi: local Windows System.Speech voice, free, timing ESTIMATED from text
  length (labelled). Used for drafts/demos when paid TTS is off.
- silent_stub: silence with estimated timing for machines with no TTS (tests/CI).
Never clones a streamer's voice.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
import subprocess
import wave

import httpx

from .config import settings
from .media import FF, NOWIN, run


def _vowel_groups(w: str) -> int:
    return max(1, len(re.findall(r"[aeiouy]+", w.lower())))


def estimate_words(text: str, duration: float, lead: float = 0.05) -> list[dict]:
    words = text.split()
    weights = [_vowel_groups(w) + (0.6 if w[-1:] in ",.?!" else 0) for w in words]
    total = sum(weights) or 1
    t, out = lead, []
    span = max(duration - lead - 0.1, 0.2)
    for w, k in zip(words, weights):
        d = span * k / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d * 0.92, 3)})
        t += d
    return out


def wav_duration(path: pathlib.Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def to_wav(src: pathlib.Path, dst: pathlib.Path) -> None:
    run(["-i", str(src), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)], timeout=120)


class ElevenLabs:
    name = "elevenlabs"

    def __init__(self, key: str | None = None, voice: str | None = None, speed: float = 1.05):
        self.key = key or settings.elevenlabs_key
        self.voice = voice or settings.elevenlabs_voice
        self.speed = speed

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def synth(self, text: str, out: pathlib.Path) -> dict:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/with-timestamps",
                       params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": self.key},
                       json={"text": text, "model_id": settings.elevenlabs_model,
                             "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35,
                                                "use_speaker_boost": True, "speed": self.speed}}, timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        d = r.json()
        mp3 = out.with_suffix(".mp3")
        mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        to_wav(mp3, out)
        al = d["alignment"]
        words, cur, ws, pe = [], "", None, 0.0
        for c, s, e in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
            if c.isspace():
                if cur:
                    words.append({"w": cur, "s": ws, "e": pe})
                cur, ws = "", None
            else:
                ws = s if ws is None else ws
                cur, pe = cur + c, e
        if cur:
            words.append({"w": cur, "s": ws, "e": pe})
        return {"words": words, "duration": wav_duration(out), "timing": "provider", "chars": len(text)}


class WindowsSapi:
    name = "windows_sapi"

    def __init__(self, rate: int = 1):
        self.rate = rate

    @property
    def configured(self) -> bool:
        import shutil
        return shutil.which("powershell") is not None

    def synth(self, text: str, out: pathlib.Path) -> dict:
        tmp = out.with_suffix(".sapi.wav")
        txt = out.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'en-US' } | Select-Object -First 1; "
              "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
              f"$s.Rate = {self.rate}; $s.SetOutputToWaveFile('{tmp}'); "
              f"$s.Speak([IO.File]::ReadAllText('{txt}')); $s.Dispose()")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=120, creationflags=NOWIN)
        if p.returncode != 0 or not tmp.exists():
            raise RuntimeError(f"Windows speech failed: {p.stderr[-300:]}")
        # trim leading/trailing silence so sentence placement is tight
        run(["-i", str(tmp), "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB,areverse", "-ac", "1", "-ar", "48000", str(out)])
        tmp.unlink(missing_ok=True)
        txt.unlink(missing_ok=True)
        dur = wav_duration(out)
        return {"words": estimate_words(text, dur), "duration": dur, "timing": "estimated", "chars": len(text)}


class SilentStub:
    name = "silent_stub"
    configured = True

    def synth(self, text: str, out: pathlib.Path) -> dict:
        dur = max(0.8, len(text.split()) / 2.7)
        run(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{dur:.2f}", "-c:a", "pcm_s16le", str(out)])
        return {"words": estimate_words(text, dur), "duration": dur, "timing": "estimated", "chars": len(text)}


def provider(name: str):
    return {"elevenlabs": ElevenLabs, "windows_sapi": WindowsSapi, "silent_stub": SilentStub}[name]()


def synth_lines(lines: list[str], folder: pathlib.Path, prov, prefix: str) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, line in enumerate(lines):
        path = folder / f"{prefix}_{i:02d}.wav"
        meta_p = path.with_suffix(".json")
        if path.exists() and meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("text") == line and meta.get("provider") == prov.name:
                out.append(meta)
                continue  # unchanged sentence: reuse (sentence-level regeneration)
        r = prov.synth(line, path)
        meta = {"text": line, "path": str(path), "provider": prov.name, **r}
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        out.append(meta)
    return out
