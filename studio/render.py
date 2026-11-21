"""Vertical Shorts renderer (1080x1920, H.264 + AAC, ~-14 LUFS) and brand graphics.

All graphics are drawn here (original): sunset gradient, palm silhouettes, the
"VI CENTRAL" badge, a chat-activity meter built from the measured chat data, the end
card, covers and thumbnails. No Rockstar logos or key art. Calm v2 channel look:
muted night palette, Bahnschrift, neon only as accent, vignette, captions on a plate.
"""
from __future__ import annotations

import json
import math
import pathlib
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import settings
from .media import run

W, H, FPS = 1080, 1920, 30
PINK, PURPLE, ORANGE, YELLOW, NIGHT = (255, 46, 151), (122, 43, 255), (255, 140, 40), (255, 225, 77), (9, 7, 15)
BAHN = r"C:\Windows\Fonts\bahnschrift.ttf"

PRESETS_DEFAULT = {
    "VIRAL GAMING": {"layout": "focus", "zoom": 0.10, "music_db": -24, "source_db": -3, "fade": 0.25, "caption_size": 92, "plate": True},
    "CINEMATIC": {"layout": "fit", "zoom": 0.06, "music_db": -22, "source_db": -4, "fade": 0.5, "caption_size": 84, "plate": True},
    "FUNNY MOMENTS": {"layout": "focus", "zoom": 0.12, "music_db": -26, "source_db": -2, "fade": 0.2, "caption_size": 94, "plate": True},
    "GAMING NEWS": {"layout": "fit", "zoom": 0.04, "music_db": -24, "source_db": -6, "fade": 0.3, "caption_size": 82, "plate": True},
    "EDUCATIONAL": {"layout": "fit", "zoom": 0.05, "music_db": -26, "source_db": -6, "fade": 0.4, "caption_size": 80, "plate": True},
}
FG_Y = 330          # top of the video window in "fit" layout
FG_H = 860          # window height: 16:9 source scaled to this height, centre-cropped to 1080 wide
METER_Y = 1210      # chat meter panel under the window
CAPTION_MARGIN_V = 440  # caption baseline ~1480 px: above the Shorts UI zone (bottom 20% = 1536+)


def presets() -> dict:
    p = settings.data_dir / "presets.json"
    if p.exists():
        return {**PRESETS_DEFAULT, **json.loads(p.read_text(encoding="utf-8"))}
    return dict(PRESETS_DEFAULT)


def save_preset(name: str, values: dict) -> dict:
    allowed = set(PRESETS_DEFAULT["CINEMATIC"])
    cur = presets()
    base = cur.get(name, PRESETS_DEFAULT["CINEMATIC"])
    cur[name] = {**base, **{k: v for k, v in values.items() if k in allowed}}
    custom = {k: v for k, v in cur.items() if PRESETS_DEFAULT.get(k) != v}
    (settings.data_dir / "presets.json").write_text(json.dumps(custom, indent=1), encoding="utf-8")
    return cur[name]


_fc: dict = {}


def font(size: int, style: str = "Bold Condensed") -> ImageFont.FreeTypeFont:
    k = (size, style)
    if k not in _fc:
        try:
            f = ImageFont.truetype(BAHN, size)
            f.set_variation_by_name(style)
        except Exception:
            f = ImageFont.load_default(size)
        _fc[k] = f
    return _fc[k]


# ---------------------------------------------------------------- brand graphics
def sunset(w: int, h: int, horizon: float = 0.62) -> Image.Image:
    y = np.linspace(0, 1, h)[:, None]
    top, mid, low = np.array([10, 9, 26]), np.array([44, 22, 66]), np.array([150, 72, 92])
    k1 = np.clip(y / horizon, 0, 1)
    k2 = np.clip((y - horizon * 0.6) / (horizon * 0.4), 0, 1)
    col = top * (1 - k1) + mid * k1
    col = col * (1 - k2) + low * k2
    col = np.where(y > horizon, NIGHT + (col - NIGHT) * np.clip(1 - (y - horizon) * 3, 0.15, 1), col)
    img = Image.fromarray(np.repeat(col[:, None, :], w, axis=1).reshape(h, w, 3).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy, r = w // 2, int(h * horizon), int(min(w, h) * 0.22)
    for i in range(r, 0, -2):  # sun with soft glow
        t = i / r
        d.ellipse([cx - i, cy - i, cx + i, cy + i], fill=(255, int(120 + 90 * (1 - t)), int(80 + 40 * (1 - t)), 10))
    d.pieslice([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=(255, 150, 90, 235))
    for k in range(5):  # sun stripes
        yy = cy - int(r * (0.12 + k * 0.16))
        d.rectangle([cx - r, yy, cx + r, yy + max(2, 6 - k)], fill=tuple(int(c) for c in mid) + (255,))
    d.rectangle([0, cy, w, h], fill=NIGHT + (255,))
    palm(d, int(w * 0.12), cy, int(h * 0.30))
    palm(d, int(w * 0.86), cy, int(h * 0.24), flip=True)
    return img


def palm(d: ImageDraw.ImageDraw, x: int, base: int, height: int, flip: bool = False) -> None:
    col = (6, 5, 12, 255)
    s = -1 if flip else 1
    pts = []
    for i in range(21):
        t = i / 20
        pts.append((x + s * int(math.sin(t * 1.3) * height * 0.12), base - int(t * height)))
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d.line([x0, y0, x1, y1], fill=col, width=max(4, int(height * 0.035 * (1.2 - (base - y0) / height))))
    tx, ty = pts[-1]
    for ang in (-160, -120, -75, -35, 10, 200, 240):
        a = math.radians(ang)
        ln = height * 0.42
        ex, ey = tx + math.cos(a) * ln, ty + math.sin(a) * ln * 0.55 + ln * 0.18
        mx, my = tx + math.cos(a) * ln * 0.5, ty + math.sin(a) * ln * 0.35 - ln * 0.05
        d.line([tx, ty, mx, my, ex, ey], fill=col, width=max(3, int(height * 0.022)), joint="curve")


def badge(size: int = 150) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=NIGHT + (235,), outline=PINK + (255,), width=max(3, size // 30))
    d.ellipse([size * 0.12, size * 0.12, size * 0.88, size * 0.88], outline=PURPLE + (200,), width=2)
    f1, f2 = font(int(size * 0.42)), font(int(size * 0.15), "SemiBold Condensed")
    d.text((size / 2, size * 0.44), "VI", font=f1, fill=YELLOW, anchor="mm")
    d.text((size / 2, size * 0.73), "CENTRAL", font=f2, fill=(255, 255, 255), anchor="mm")
    return img


def plate_text(d: ImageDraw.ImageDraw, xy, text: str, size: int, fill=(255, 255, 255), plate=(9, 7, 15, 200),
               pad: int = 24, anchor: str = "mm", max_w: int | None = None) -> None:
    f = font(size)
    while max_w and d.textlength(text, font=f) > max_w and size > 30:
        size -= 4
        f = font(size)
    box = d.textbbox(xy, text, font=f, anchor=anchor)
    d.rounded_rectangle([box[0] - pad, box[1] - pad * 0.6, box[2] + pad, box[3] + pad * 0.6], radius=18, fill=plate)
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


def hook_card(headline: str, sub: str, out: pathlib.Path) -> pathlib.Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    words = headline.upper().split()
    lines, cur = [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=font(104)) > W * 0.8 and cur:
            lines.append(cur)
            cur = w_
        else:
            cur = t
    lines.append(cur)
    y = 190
    for i, ln in enumerate(lines[:2]):
        plate_text(d, (W * 0.46, y + i * 128), ln, 104, YELLOW if i == len(lines[:2]) - 1 else (255, 255, 255), max_w=int(W * 0.8))
    if sub:
        plate_text(d, (W * 0.46, y + len(lines[:2]) * 128 + 10), sub, 46, (235, 230, 255), (40, 20, 70, 210), pad=16)
    img.save(out)
    return out


def chat_meter(points: list[dict], event_at: float, out: pathlib.Path, label: str = "LIVE CHAT SPEED") -> dict:
    """Panel showing real messages/second across the clip; a playhead overlay moves on it.
    Returns geometry so the renderer can animate the playhead."""
    pw, ph = int(W * 0.84), 150
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=20, fill=(9, 7, 15, 205), outline=(122, 43, 255, 160), width=2)
    d.text((22, 14), label, font=font(30, "SemiBold Condensed"), fill=(200, 190, 230))
    vals = [p["v"] for p in points] or [0]
    vmax = max(max(vals), 1)
    gx0, gx1, gy0, gy1 = 22, pw - 22, 54, ph - 16
    n = max(len(vals) - 1, 1)
    pts = [(gx0 + (gx1 - gx0) * i / n, gy1 - (gy1 - gy0) * v / vmax) for i, v in enumerate(vals)]
    if len(pts) > 1:
        d.polygon(pts + [(gx1, gy1), (gx0, gy1)], fill=(255, 46, 151, 70))
        d.line(pts, fill=PINK + (255,), width=4, joint="curve")
    peak = max(vals)
    d.text((pw - 22, 14), f"PEAK {peak:.0f}/s", font=font(30, "SemiBold Condensed"), fill=YELLOW, anchor="ra")
    img.save(out)
    return {"x": (W - pw) // 2 + gx0, "w": gx1 - gx0, "y": METER_Y + gy0, "h": gy1 - gy0, "panel_w": pw}


def end_card(out: pathlib.Path, line: str = "Follow for daily GTA 6 breakdowns") -> pathlib.Path:
    hz = 0.50
    img = sunset(W, H, hz).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    # water: shimmering sun reflection fills the lower half (no empty band)
    y0 = int(H * hz)
    for i, y in enumerate(range(y0 + 8, H - 80, 22)):
        k = 1 - (y - y0) / (H - y0)
        half = int(W * (0.10 + 0.22 * (1 - k)) * (0.75 + 0.25 * math.sin(i * 1.7)))
        col = (255, int(120 + 60 * k), int(90 + 40 * k), int(40 + 150 * k))
        d.rounded_rectangle([W // 2 - half, y, W // 2 + half, y + max(3, int(9 * k))], radius=4, fill=col)
    for i, y in enumerate(range(y0 + 40, H, 90)):  # faint neon road lines toward the viewer
        d.line([W // 2 - 60 - i * 70, y, W // 2 - 40 - i * 90, y + 50], fill=PINK + (60,), width=3)
        d.line([W // 2 + 60 + i * 70, y, W // 2 + 40 + i * 90, y + 50], fill=PURPLE + (70,), width=3)
    img.alpha_composite(badge(280), ((W - 280) // 2 - 40, 300))
    plate_text(d, (W * 0.46, 1180), "GTA VI CENTRAL", 112, (255, 255, 255), max_w=int(W * 0.78))
    plate_text(d, (W * 0.46, 1320), line, 50, YELLOW, max_w=int(W * 0.78))
    vignette(img)
    img.convert("RGB").save(out)
    return out


def vignette(img: Image.Image, strength: float = 0.55) -> None:
    w, h = img.size
    y, x = np.ogrid[:h, :w]
    r = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
    a = (np.clip((r - 0.65) / 0.75, 0, 1) * 255 * strength).astype(np.uint8)
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade.putalpha(Image.fromarray(a))
    img.alpha_composite(shade)


def vignette_png(out: pathlib.Path) -> pathlib.Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vignette(img, 0.6)
    img.save(out)
    return out


def cover(frame: pathlib.Path | None, text: str, out: pathlib.Path) -> pathlib.Path:
    """Shorts cover (1080x1920): authorized frame + <= 3 words."""
    words = text.upper().split()[:3]
    if frame and frame.exists():
        src = Image.open(frame).convert("RGB")
        bg = src.resize((int(src.width * H / src.height), H)).crop((0, 0, W, H)) if src.width / src.height < W / H else \
            src.resize((int(src.width * H / src.height), H))
        bg = bg.crop(((bg.width - W) // 2, 0, (bg.width - W) // 2 + W, H)).filter(ImageFilter.GaussianBlur(28))
        bg = Image.blend(bg, Image.new("RGB", (W, H), NIGHT), 0.45).convert("RGBA")
        fg = src.resize((W, int(src.height * W / src.width)))
        bg.alpha_composite(fg.convert("RGBA"), (0, FG_Y + 60))
    else:
        bg = sunset(W, H).convert("RGBA")
    d = ImageDraw.Draw(bg)
    for i, wd in enumerate(words):
        plate_text(d, (W * 0.46, 250 + i * 150), wd, 150, YELLOW if i == len(words) - 1 else (255, 255, 255),
                   max_w=int(W * 0.8))
    bg.alpha_composite(badge(140), (40, 40))
    vignette(bg, 0.45)
    bg.convert("RGB").save(out, quality=95)
    return out


def thumbnail(frame: pathlib.Path | None, text: str, out: pathlib.Path) -> pathlib.Path:
    tw, th = 1280, 720
    if frame and frame.exists():
        img = Image.open(frame).convert("RGB").resize((tw, th)).convert("RGBA")
    else:
        img = sunset(tw, th).convert("RGBA")
    shade = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(tw // 2):
        sd.line([x, 0, x, th], fill=(9, 7, 15, int(200 * (1 - x / (tw / 2)))))
    img.alpha_composite(shade)
    d = ImageDraw.Draw(img)
    for i, wd in enumerate(text.upper().split()[:3]):
        plate_text(d, (60, 150 + i * 150), wd, 130, YELLOW if i == len(text.split()[:3]) - 1 else (255, 255, 255),
                   anchor="lm", plate=(0, 0, 0, 0), pad=0)
    img.alpha_composite(badge(120), (tw - 150, 30))
    img.convert("RGB").save(out, quality=95)
    return out


# ---------------------------------------------------------------- audio beds
def write_wav(path: pathlib.Path, x: np.ndarray, sr: int = 48000) -> None:
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def read_wav(path: pathlib.Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def original_music(duration: float, out: pathlib.Path, sr: int = 48000, seed: int = 3) -> pathlib.Path:
    """Original ambient synth bed (generated here, no third-party music)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration * sr)) / sr
    chords = [[220.0, 277.18, 329.63], [196.0, 246.94, 293.66], [174.61, 220.0, 261.63], [196.0, 246.94, 329.63]]
    x = np.zeros_like(t)
    bar = 4.0
    for i, ch in enumerate(chords * int(duration // (bar * 4) + 1)):
        t0 = i * bar
        if t0 >= duration:
            break
        m = (t >= t0) & (t < t0 + bar + 1.5)
        tt = t[m] - t0
        env = np.minimum(tt / 0.8, 1) * np.exp(-np.maximum(tt - bar, 0) * 2)
        for f in ch:
            x[m] += env * (np.sin(2 * np.pi * f * tt) * 0.5 + np.sin(2 * np.pi * f * 2.003 * tt) * 0.12)
    beat = (np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 1.0) * 9))) * 0.6  # soft kick pulse
    x = x / 3 + beat + rng.normal(0, 0.004, len(t))
    fade = np.minimum(1, np.minimum(t / 1.5, (duration - t) / 2.0))
    write_wav(out, x * fade * 0.5, sr)
    return out


def place_lines(lines: list[dict], total: float, out: pathlib.Path, sr: int = 48000) -> pathlib.Path:
    """Mix per-sentence narration wavs at their start offsets into one track."""
    track = np.zeros(int(total * sr) + sr, dtype=np.float32)
    for ln in lines:
        x, r = read_wav(pathlib.Path(ln["path"]))
        if r != sr:
            idx = np.linspace(0, len(x) - 1, int(len(x) * sr / r))
            x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
        i = int(ln["start"] * sr)
        j = min(len(track), i + len(x))
        track[i:j] += x[: j - i]
    peak = np.abs(track).max() or 1
    write_wav(out, track / peak * 0.89, sr)
    return out


# ---------------------------------------------------------------- reframe
def focus_track(motion_cx: list[float], src_w: int, src_h: int, step: float) -> str:
    """Piecewise-linear crop x(t) following the motion centroid, smoothed (no jerky pans)."""
    cw = src_h * 9 / 16
    if not motion_cx:
        return f"{(src_w - cw) / 2:.0f}"
    xs = np.array(motion_cx, dtype=float)
    k = np.ones(5) / 5
    xs = np.convolve(np.pad(xs, 2, mode="edge"), k, mode="valid")
    xs = np.clip(xs * src_w - cw / 2, 0, src_w - cw)
    keys = [(i * step, float(x)) for i, x in enumerate(xs)]
    expr = f"{keys[-1][1]:.0f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(keys, keys[1:]))):
        expr = f"if(lt(t,{t1:.2f}),{x0:.0f}+({x1 - x0:.0f})*(t-{t0:.2f})/{t1 - t0:.2f},{expr})"
    return expr


def motion_centroids(path: str, step: float = 1.0) -> list[float]:
    """Normalised x of motion energy per `step` seconds (cheap 160x90 analysis)."""
    import subprocess
    from .media import FF, NOWIN
    w, h = 160, 90
    p = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-i", path, "-vf", f"fps={4},scale={w}:{h},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, creationflags=NOWIN)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    if n < 3:
        return []
    fr = buf[: n * w * h].reshape(n, h, w).astype(np.float32)
    diff = np.abs(np.diff(fr, axis=0)).sum(1)  # (n-1, w)
    per = max(1, int(4 * step))
    out = []
    xs = np.arange(w) / w
    for i in range(0, len(diff), per):
        col = diff[i:i + per].sum(0)
        out.append(float((col * xs).sum() / col.sum()) if col.sum() > 1 else 0.5)
    return out


# ---------------------------------------------------------------- main render
def render_short(folder: pathlib.Path, clip: pathlib.Path, narration_wav: pathlib.Path, ass: pathlib.Path,
                 hook_png: pathlib.Path, meter_png: pathlib.Path, meter_geo: dict, endcard_png: pathlib.Path,
                 music_wav: pathlib.Path, event_at: float, clip_dur: float, preset: dict, out: pathlib.Path,
                 src_w: int, src_h: int, end_s: float = 2.5) -> dict:
    total = clip_dur + end_s
    badge_png = folder / "_badge.png"
    badge(120).save(badge_png)
    vig = vignette_png(folder / "_vignette.png")
    head = folder / "_playhead.png"
    Image.new("RGBA", (5, int(meter_geo["h"])), YELLOW + (245,)).save(head)
    z = float(preset["zoom"])
    zoom = f"zoompan=z='1+{z}*exp(-pow((in_time-{event_at:.2f})/0.9\\,2))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:fps={FPS}"
    if preset["layout"] == "focus":
        xexpr = focus_track(motion_centroids(str(clip)), src_w, src_h, 1.0).replace(",", "\\,")
        fg = (f"[0:v]crop=w='ih*9/16':h=ih:x='{xexpr}':y=0,scale={W}:{H},setsar=1,{zoom}:s={W}x{H}[fg];"
              f"[fg]copy[base];")
    else:
        fh = FG_H
        fg = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:2,"
              f"eq=brightness=-0.16:saturation=0.75[bg];[b]scale=-2:{fh},crop={W}:{fh},setsar=1,{zoom}:s={W}x{fh}[fg];"
              f"[bg][fg]overlay=0:{FG_Y}[base];")
    gx, gw = meter_geo["x"], meter_geo["w"]
    fade = float(preset["fade"])
    vf = (fg +
          f"[base][5:v]overlay=0:0[v0];"
          f"[v0][3:v]overlay=x={(W - meter_geo['panel_w']) // 2}:y={METER_Y}[v1];"
          f"[v1][8:v]overlay=x='{gx}+{gw}*t/{clip_dur:.2f}':y={meter_geo['y']}[v2];"
          f"[v2][4:v]overlay=36:40[v3];"
          f"[v3][2:v]overlay=0:0:enable='lt(t,3.6)'[v4];"
          f"[v4]ass={ass.name},fade=t=in:st=0:d={fade},fade=t=out:st={clip_dur - fade:.2f}:d={fade},trim=0:{clip_dur:.3f},setpts=PTS-STARTPTS,scale={W}:{H},setsar=1,fps={FPS},format=yuv420p[main];"
          f"[6:v]scale={W}:{H},setsar=1,fade=t=in:st=0:d=0.3,format=yuv420p,trim=0:{end_s},setpts=PTS-STARTPTS[end];"
          f"[main][end]concat=n=2:v=1:a=0[vout];")
    src_gain = 10 ** (float(preset["source_db"]) / 20)
    mus_gain = 10 ** (float(preset["music_db"]) / 20)
    af = (f"[0:a]aresample=48000,pan=mono|c0=0.5*c0+0.5*c1,volume={src_gain:.3f},apad=whole_dur={total:.2f}[src];"
          f"[1:a]aresample=48000,apad=whole_dur={total:.2f},asplit[n1][n2];"
          f"[src][n1]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[duck];"
          f"[7:a]aresample=48000,volume={mus_gain:.3f},apad=whole_dur={total:.2f}[mus];"
          f"[duck][n2][mus]amix=inputs=3:duration=first:normalize=0,atrim=0:{total:.2f},"
          f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    args = ["-i", str(clip), "-i", str(narration_wav), "-loop", "1", "-t", f"{clip_dur:.2f}", "-i", str(hook_png),
            "-loop", "1", "-t", f"{clip_dur:.2f}", "-i", str(meter_png), "-loop", "1", "-t", f"{clip_dur:.2f}", "-i", str(badge_png),
            "-loop", "1", "-t", f"{clip_dur:.2f}", "-i", str(vig), "-loop", "1", "-t", f"{end_s}", "-framerate", str(FPS), "-i", str(endcard_png),
            "-i", str(music_wav), "-loop", "1", "-t", f"{clip_dur:.2f}", "-i", str(head), "-filter_complex", vf + af, "-map", "[vout]", "-map", "[aout]", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)]
    import subprocess
    import time as _t
    from .media import FF, NOWIN
    t0 = _t.time()
    p = subprocess.run([FF, "-hide_banner", "-nostdin", "-y", *args], cwd=str(folder), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NOWIN, timeout=1800)
    (folder / "_render.log").write_text(p.stderr, encoding="utf-8")
    if p.returncode != 0:
        errs = [ln for ln in p.stderr.splitlines() if any(k in ln.lower() for k in ("error", "invalid", "fail", "no such", "unable", "cannot"))]
        raise RuntimeError("render failed: " + " | ".join(errs[-8:]) + f" (full log: {folder / '_render.log'})")
    # second pass: measure integrated loudness and correct to -14 LUFS (single-pass loudnorm
    # undershoots on short clips with pauses); video is stream-copied
    from .media import loudness
    for _ in range(2):
        lufs = loudness(out)
        if lufs is None or abs(lufs + 14) <= 0.6:
            break
        fixed = out.with_name("_loud_fix.mp4")
        run(["-i", str(out), "-c:v", "copy", "-af", f"volume={-14 - lufs:.2f}dB,alimiter=limit=0.89:level=false",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(fixed)])
        fixed.replace(out)
    return {"seconds": round(_t.time() - t0, 1), "command": "ffmpeg " + " ".join(args)[:4000], "log": p.stderr[-1500:]}


def contact_sheet(video: pathlib.Path, out: pathlib.Path, n: int = 12, cols: int = 6) -> pathlib.Path:
    from .media import probe
    dur = probe(video)["duration"]
    tw, th = 270, 480
    rows = math.ceil(n / cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 34)), NIGHT)
    d = ImageDraw.Draw(sheet)
    tmp = out.parent / "_cs"
    tmp.mkdir(exist_ok=True)
    for i in range(n):
        t = dur * (i + 0.5) / n
        f = tmp / f"f{i}.jpg"
        run(["-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={tw}:{th}", str(f)], timeout=60)
        sheet.paste(Image.open(f), ((i % cols) * tw, (i // cols) * (th + 34)))
        d.text(((i % cols) * tw + 8, (i // cols) * (th + 34) + th + 4), f"{t:5.1f}s", font=font(24, "SemiBold"), fill=(230, 230, 230))
        f.unlink()
    tmp.rmdir()
    sheet.save(out)
    return out
