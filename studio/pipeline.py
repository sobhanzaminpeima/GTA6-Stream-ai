"""Clip extraction + the GENERATE SHORT workflow as resumable background jobs.

Stages persist their outputs in ClipProject.stages; re-running a project skips
completed stages, and `regenerate(project, stage)` clears that stage and everything
downstream of it without losing earlier work.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

from sqlalchemy import select

from . import commentary, costs, events, media, narration, packaging, render, rights, scoring, subtitles
from .config import settings
from .db import audit, automation, notify, session
from .jobs import handler
from .models import (AIScript, ChatEvent, ChatWindow, ClipProject, ClipTimeline, Livestream, MediaAsset, MediaSegment,
                     ProductionCost, RenderJob, SubtitleTrack, ViralCandidate, VideoExport, Voiceover)

STAGES = ["verify_rights", "retrieve_footage", "analyze", "select_cuts", "commentary", "narration", "captions",
          "timeline", "graphics", "render", "cover", "metadata", "qc", "review"]


class PermanentError(RuntimeError):
    permanent = True


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- extraction
def cut_plan(event: float, avail_lo: float, avail_hi: float, pre: float = 15, post: float = 25,
             scenes: list[float] | None = None) -> list[dict]:
    """A (20 s), B (35 s), C (50 s) cuts around the event, only where media supports them.
    In-points snap back to a scene boundary within 3 s (clean shot start); never stretched."""
    plans = [("A", 7, 13), ("B", min(pre, 15), 20), ("C", pre, max(post, 35))]
    out = []
    for label, b, a in plans:
        i, o = event - b, event + a
        for sc in sorted(scenes or [], reverse=True):
            if i - 3 <= sc <= i:
                i = sc
                break
        if i < avail_lo - 0.05 or o > avail_hi + 0.05:
            continue
        out.append({"label": label, "in": round(i, 2), "out": round(o, 2), "duration": round(o - i, 2),
                    "event_at": round(event - i, 2)})
    return out


@handler("extract_clip")
def extract_clip_job(payload: dict, ctx) -> dict:
    cid = payload["candidate_id"]
    with session() as s:
        c = s.get(ViralCandidate, cid)
        ls = s.get(Livestream, c.livestream_id)
        rights.require_edit(s, ls)
        a = automation(s)
        segs = [{"id": x.id, "path": x.path, "media_start": x.media_start, "duration": x.duration}
                for x in s.scalars(select(MediaSegment).where(MediaSegment.session_id == c.session_id,
                                                              MediaSegment.deleted.is_(False)).order_by(MediaSegment.media_start))]
        event = c.media_ts
        src_file = ls.media_source if (not segs and ls.media_source and pathlib.Path(ls.media_source).exists()) else None
    if event is None:
        raise PermanentError("candidate has no media timestamp")
    folder = settings.data_dir / "clips" / f"cand_{cid}"
    folder.mkdir(parents=True, exist_ok=True)
    pre, post = float(a["pre_roll_s"]), float(a["post_roll_s"])
    lo, hi = event - pre - 3, event + max(post, 35)
    wide = folder / "source_wide.mp4"
    ctx.progress(0.1, "cutting from the rolling buffer" if segs else "cutting from the authorized file")
    if segs:
        r = media.extract_clip(segs, lo, hi, wide)
        w_in, w_out = r["media_in"], r["media_out"]
    elif src_file:
        dur = media.probe(src_file)["duration"]
        w_in, w_out = max(0.0, lo), min(dur, hi)
        media.run(["-ss", f"{w_in:.3f}", "-i", src_file, "-t", f"{w_out - w_in:.3f}", "-c:v", "libx264", "-preset", "fast",
                   "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(wide)])
    else:
        raise PermanentError("no authorized media available for this candidate")
    ctx.progress(0.5, "analysing audio + visuals")
    scenes = [w_in + t for t in media.scene_boundaries(str(wide))]
    cuts = cut_plan(event, w_in, w_out, pre, post, scenes)
    if not cuts:
        raise PermanentError(f"buffer covers {w_in:.1f}-{w_out:.1f}s, not enough around the event at {event:.1f}s")
    ref = next((x for x in cuts if x["label"] == "B"), cuts[0])
    fa = media.audio_features(media.decode_audio(str(wide), ss=ref["in"] - w_in, t=ref["duration"]))
    a_score, a_tags = media.audio_excitement(fa)
    fv = media.visual_features(str(wide), ss=ref["in"] - w_in, t=ref["duration"])
    v_score, v_tags = media.visual_importance(fv)
    info = media.probe(wide)
    with session() as s:
        c = s.get(ViralCandidate, cid)
        ls = s.get(Livestream, c.livestream_id)
        c.audio_score, c.visual_score = a_score, v_score
        no_chat = ls.platform == "upload" or (c.explanation or {}).get("no_chat")
        res = scoring.combine({"chat": None if no_chat else c.chat_score, "audio": a_score, "visual": v_score, "narrative": c.narrative_score,
                               "originality": c.originality_score}, a["score_weights"],
                              {"audio": ", ".join(a_tags), "visual": ", ".join(v_tags)})
        c.overall_score, c.confidence, c.tier = res.overall, res.confidence, res.tier
        exp = dict(c.explanation or {})
        exp.update(score=res.explanation, weights=res.weights_used, media={"audio": a_tags, "visual": v_tags, "scenes": scenes[:20],
                                                                          "cuts": cuts, "wide_in": w_in, "wide_out": w_out})
        c.explanation = exp
        c.clip_readiness = "clip_ready"
        s.add(MediaAsset(kind="candidate_clip", path=str(wide), sha256=sha256(wide), duration=info["duration"],
                         width=info["width"], height=info["height"], candidate_id=cid,
                         meta={"media_in": w_in, "media_out": w_out, "cuts": cuts}))
        for x in s.scalars(select(MediaSegment).where(MediaSegment.id.in_(payload.get("segment_ids", [])))):
            x.pinned_by = [p for p in (x.pinned_by or []) if p != f"cand{cid}"]
        audit(s, "clip_extracted", f"candidate:{cid}", cuts=[x["label"] for x in cuts])
        auto = a.get("auto_generate_drafts") and c.overall_score >= a["min_candidate_score"]
    events.publish("candidate", {"id": cid, "clip_ready": True})
    if auto:
        pid = create_project(cid, "CINEMATIC")
        from .jobs import enqueue
        enqueue("generate_short", {"project_id": pid})
    return {"cuts": cuts, "audio": a_score, "visual": v_score}


# ---------------------------------------------------------------- projects
def create_project(candidate_id: int, preset: str = "CINEMATIC", cut: str = "B") -> int:
    with session() as s:
        c = s.get(ViralCandidate, candidate_id)
        p = ClipProject(candidate_id=candidate_id, preset=preset, status="queued", stages={})
        s.add(p)
        s.flush()
        p.folder = str(settings.data_dir / "projects" / f"project_{p.id}")
        pathlib.Path(p.folder).mkdir(parents=True, exist_ok=True)
        s.add(ClipTimeline(project_id=p.id, selected_cut=cut))
        s.add(ProductionCost(project_id=p.id, estimated_usd=costs.estimate_short()["total_usd"], breakdown={}))
        c.production_status = "in_production"
        audit(s, "project_created", f"project:{p.id}", candidate=candidate_id, preset=preset)
        return p.id


def regenerate(project_id: int, stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(stage)
    with session() as s:
        p = s.get(ClipProject, project_id)
        st = dict(p.stages or {})
        for k in STAGES[STAGES.index(stage):]:
            st.pop(k, None)
        p.stages, p.status = st, "queued"


def _done(p: ClipProject, stage: str, output: dict) -> None:
    st = dict(p.stages or {})
    st[stage] = {"status": "done", "output": output, "at": time.time()}
    p.stages, p.stage = st, stage
    p.progress = round((STAGES.index(stage) + 1) / len(STAGES), 3)


def _tts_provider(s, project_id: int, chars: int):
    a = automation(s)
    pref = (a.get("narrator_preset") or {}).get("provider", "elevenlabs")
    if pref == "elevenlabs" and settings.elevenlabs_key:
        try:
            costs.check_budget(s, "elevenlabs", costs.tts_cost(chars))
            return narration.ElevenLabs(speed=float((a.get("narrator_preset") or {}).get("speed", 1.05))), None
        except (costs.PaidCallNotAllowed, costs.BudgetExceeded) as e:
            note = str(e)
    else:
        note = "ElevenLabs not selected or not configured"
    sapi = narration.WindowsSapi()
    if sapi.configured:
        return sapi, note + " -> local Windows voice (draft quality)"
    return narration.SilentStub(), note + " -> silent stub (no TTS available)"


@handler("generate_short")
def generate_short(payload: dict, ctx) -> dict:
    pid = payload["project_id"]
    for stage in STAGES:
        with session() as s:
            p = s.get(ClipProject, pid)
            if (p.stages or {}).get(stage, {}).get("status") == "done":
                continue
            p.status, p.stage = "running", stage
        ctx.progress(STAGES.index(stage) / len(STAGES), stage.replace("_", " "))
        STAGE_FN[stage](pid)
    with session() as s:
        p = s.get(ClipProject, pid)
        return {"status": p.status, "folder": p.folder}


def _ctx(s, pid):
    p = s.get(ClipProject, pid)
    c = s.get(ViralCandidate, p.candidate_id)
    ls = s.get(Livestream, c.livestream_id)
    return p, c, ls, pathlib.Path(p.folder)


def st_verify_rights(pid):
    with session() as s:
        p, c, ls, _ = _ctx(s, pid)
        try:
            d = rights.require_edit(s, ls)
        except rights.RightsError as e:
            p.status = "blocked_rights"
            raise PermanentError(str(e))
        if not d.can_publish_youtube:
            p.status = "blocked_rights"
            raise PermanentError("rights do not cover YouTube distribution")
        _done(p, "verify_rights", d.as_dict())


def st_retrieve(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        asset = s.scalar(select(MediaAsset).where(MediaAsset.candidate_id == c.id, MediaAsset.kind == "candidate_clip")
                         .order_by(MediaAsset.id.desc()))
        if asset is None or not pathlib.Path(asset.path).exists():
            raise PermanentError("no authorized clip yet: the candidate has no extracted footage (request it from the owner)")
        if sha256(pathlib.Path(asset.path)) != asset.sha256:
            raise PermanentError("source clip changed on disk (hash mismatch)")
        _done(p, "retrieve_footage", {"asset_id": asset.id, "path": asset.path, **(asset.meta or {})})


def st_analyze(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        src = p.stages["retrieve_footage"]["output"]
        info = media.probe(src["path"])
        exp = c.explanation or {}
        tags = exp.get("media", {})
        frames = []
        cut_b = next((x for x in src["cuts"] if x["label"] == "B"), src["cuts"][0])
        ev_rel = c.media_ts - src["media_in"]
        for i, off in enumerate((-4, 0, 3)):
            f = folder / f"frame_{i}.jpg"
            media.grab_frame(src["path"], ev_rel + off, f, 1280)
            frames.append(str(f))
        _done(p, "analyze", {"probe": info, "audio_tags": tags.get("audio", []), "visual_tags": tags.get("visual", []),
                             "frames": frames, "event_rel": ev_rel, "default_cut": cut_b["label"]})


def st_cuts(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        src = p.stages["retrieve_footage"]["output"]
        tl = s.scalar(select(ClipTimeline).where(ClipTimeline.project_id == pid).order_by(ClipTimeline.id.desc()))
        cuts = tl.cuts or src["cuts"]
        sel = next((x for x in cuts if x["label"] == tl.selected_cut), None) or next((x for x in cuts if x["label"] == "B"), cuts[0])
        tl.cuts = cuts
        clip = folder / "clip.mp4"
        media.run(["-ss", f"{sel['in'] - src['media_in']:.3f}", "-i", src["path"], "-t", f"{sel['duration']:.3f}",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                   "-ar", "48000", str(clip)])
        _done(p, "select_cuts", {"cut": sel, "clip": str(clip), "all_cuts": cuts})


def st_commentary(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        lines = [e.normalized for e in s.scalars(select(ChatEvent).where(
            ChatEvent.session_id == c.session_id, ChatEvent.is_spam.is_(False), ChatEvent.reaction_weight > 0,
            ChatEvent.ts.between(c.first_spike_ts - 10, c.last_spike_ts + 5)).limit(40))]
        an = p.stages["analyze"]["output"]
        ev = commentary.evidence_pack(c, ls, lines, {"audio": an["audio_tags"], "visual": an["visual_tags"]}, None)
        a = automation(s)
        provider, model, usage, note = "local_writer", None, None, None
        script = None
        if settings.anthropic_key or a.get("allow_paid_llm"):
            try:
                costs.check_budget(s, "anthropic", costs.llm_cost(settings.llm_model, 6000, 1500))
                script, usage = commentary.claude_writer(ev, [pathlib.Path(f) for f in an["frames"]])
                provider, model = "anthropic", usage["model"]
                costs.record(s, "anthropic", "messages.create", usage["input_tokens"] + usage["output_tokens"], "tokens",
                             costs.llm_cost(settings.llm_model, usage["input_tokens"], usage["output_tokens"]), project_id=pid)
            except Exception as e:  # budget off / no key / API error -> evidence-only local writer
                note = f"{type(e).__name__}: {e}"
        if script is None:
            script = commentary.local_writer(ev)
        hs = commentary.hookscore(script["hooks"])
        best = max(hs, key=lambda r: r["score"] or 0)
        if best.get("hook") and best["hook"] in script["hooks"]:
            script["intro_lines"][0] = best["hook"]  # strongest hook opens the Short
        s.add(AIScript(project_id=pid, provider=provider, model=model, context_summary=script["context_summary"],
                       hooks=script["hooks"], structure=script["structure"],
                       script="\n".join(script["intro_lines"] + ["[MOMENT]"] + script["outro_lines"]),
                       graphics=script["graphics"], payoff=script["payoff"], claims=script["claims"]))
        p.structure = script["structure"]
        (folder / "script.json").write_text(json.dumps({"evidence": ev, "script": script, "hookscore": hs}, indent=1,
                                                       ensure_ascii=False), encoding="utf-8")
        _done(p, "commentary", {"provider": provider, "model": model, "note": note, "script": script, "hookscore": hs,
                                "evidence": ev})


def st_narration(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        sc = p.stages["commentary"]["output"]["script"]
        cut = p.stages["select_cuts"]["output"]["cut"]
        chars = sum(len(x) for x in sc["intro_lines"] + sc["outro_lines"])
        prov, note = _tts_provider(s, pid, chars)
    intro = narration.synth_lines(sc["intro_lines"], folder / "vo", prov, "intro")
    outro = narration.synth_lines(sc["outro_lines"], folder / "vo", prov, "outro")
    # place: intro from 0.35 s, must end ~1 s before the moment; outro starts ~5 s after it
    ev = cut["event_at"]
    t, placed = 0.35, []
    for ln in intro:
        placed.append({**ln, "start": t, "part": "intro"})
        t += ln["duration"] + 0.18
    overflow = t - (ev - 0.8)
    if overflow > 0 and len(placed) > 1:  # drop the last intro line rather than talk over the moment
        placed.pop()
        t = placed[-1]["start"] + placed[-1]["duration"]
    t = max(ev + 5.0, t + 0.5)
    for ln in outro:
        placed.append({**ln, "start": t, "part": "outro"})
        t += ln["duration"] + 0.2
    clip_dur = max(cut["duration"], t + 0.6)
    full = folder / "narration.wav"
    render.place_lines(placed, clip_dur, full)
    words = []
    for ln in placed:
        words += [{"w": w["w"], "s": round(w["s"] + ln["start"], 3), "e": round(w["e"] + ln["start"], 3)} for w in ln["words"]]
    with session() as s:
        p = s.get(ClipProject, pid)
        chars_paid = chars if prov.name == "elevenlabs" else 0
        if chars_paid:
            costs.record(s, "elevenlabs", "tts.with-timestamps", chars_paid, "characters", costs.tts_cost(chars_paid), project_id=pid)
        else:
            costs.record(s, "local", f"tts.{prov.name}", chars, "characters", 0.0, project_id=pid)
        s.add(Voiceover(project_id=pid, provider=prov.name, voice=getattr(prov, "voice", prov.name), path=str(full),
                        words=words, duration=clip_dur, timing_source=intro[0]["timing"] if intro else "estimated"))
        _done(p, "narration", {"provider": prov.name, "note": note, "path": str(full), "lines": placed, "words": words,
                               "clip_duration": clip_dur, "timing": intro[0]["timing"] if intro else "estimated"})


def st_captions(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        words = p.stages["narration"]["output"]["words"]
        pr = render.presets().get(p.preset, render.PRESETS_DEFAULT["CINEMATIC"])
        style = {"size": pr["caption_size"], "plate": pr["plate"], "margin_v": render.CAPTION_MARGIN_V}
        existing = s.scalar(select(SubtitleTrack).where(SubtitleTrack.project_id == pid, SubtitleTrack.edited.is_(True)))
        cues = existing.cues if existing else subtitles.chunk(words)
        srt, ass = subtitles.write(cues, folder, style)
        if not existing:
            s.add(SubtitleTrack(project_id=pid, srt_path=str(srt), ass_path=str(ass), cues=cues, style=style))
        _done(p, "captions", {"srt": str(srt), "ass": str(ass), "cues": len(cues), "style": style,
                              "safe_zone": subtitles.safe_zone_ok(style)})


def st_timeline(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        cut = p.stages["select_cuts"]["output"]["cut"]
        nar = p.stages["narration"]["output"]
        pr = render.presets().get(p.preset, render.PRESETS_DEFAULT["CINEMATIC"])
        tl = s.scalar(select(ClipTimeline).where(ClipTimeline.project_id == pid).order_by(ClipTimeline.id.desc()))
        tl.reframe = {"layout": pr["layout"], "zoom_at": cut["event_at"], "zoom": pr["zoom"]}
        tl.overlays = [{"type": "hook_card", "from": 0, "to": 3.6}, {"type": "chat_meter", "from": 0, "to": nar["clip_duration"]},
                       {"type": "badge", "from": 0, "to": nar["clip_duration"]}, {"type": "end_card", "duration": 2.5}]
        _done(p, "timeline", {"layout": pr["layout"], "preset": p.preset, "clip_duration": nar["clip_duration"],
                              "event_at": cut["event_at"], "overlays": tl.overlays})


def st_graphics(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        sc = p.stages["commentary"]["output"]["script"]
        ev = p.stages["commentary"]["output"]["evidence"]
        cut = p.stages["select_cuts"]["output"]["cut"]
        dur = p.stages["timeline"]["output"]["clip_duration"]
        # chat meter from the real per-second chat data across the cut
        c_in_wall = c.event_ts - cut["event_at"]
        rows = s.scalars(select(ChatWindow).where(ChatWindow.session_id == c.session_id,
                                                  ChatWindow.second.between(int(c_in_wall) - 2, int(c_in_wall + dur) + 2))
                         .order_by(ChatWindow.second)).all()
        lat = (c.explanation or {}).get("timing", {}).get("chat_latency_s", 0)
        pts = [{"v": r.messages} for r in rows]
    head = sc.get("cover_text") or "CHAT EXPLODED"
    if ev["chat"] is None:  # owner upload: show the real audio level instead of chat
        x = media.audio_features(media.decode_audio(str(p.stages["select_cuts"]["output"]["clip"])), frame_s=0.5)
        db = x.get("rms_db") or [0]
        lo = min(db)
        pts = [{"v": max(0.0, v - lo)} for v in db]
        sub, label = ev["game_label"], "AUDIO LEVEL"
    else:
        mult = ev["chat"]["multiple_of_baseline"] or 1
        sub = f"CHAT x{mult:.0f} IN 10 SECONDS" if mult >= 2 else ev["game_label"]
        label = "LIVE CHAT SPEED" + (" (SIMULATED)" if ev["stream"]["simulation"] else "")
    hook = render.hook_card(head, sub, folder / "_hook.png")
    geo = render.chat_meter(pts, cut["event_at"], folder / "_meter.png", label)
    endc = render.end_card(folder / "_endcard.png")
    music = render.original_music(dur + 2.5, folder / "_music.wav")
    with session() as s:
        p = s.get(ClipProject, pid)
        _done(p, "graphics", {"hook": str(hook), "meter": str(folder / "_meter.png"), "meter_geo": geo, "end_card": str(endc),
                              "music": str(music), "meter_points": len(pts), "chat_latency_s": lat})


def st_render(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        g, cut = p.stages["graphics"]["output"], p.stages["select_cuts"]["output"]
        tl, nar, cap = p.stages["timeline"]["output"], p.stages["narration"]["output"], p.stages["captions"]["output"]
        info = p.stages["analyze"]["output"]["probe"]
        pr = render.presets().get(p.preset, render.PRESETS_DEFAULT["CINEMATIC"])
        rj = RenderJob(project_id=pid, status="running", attempts=1)
        s.add(rj)
        s.flush()
        rjid = rj.id
    clip = pathlib.Path(cut["clip"])
    dur = tl["clip_duration"]
    if dur > cut["cut"]["duration"] + 0.05:  # outro needs a little more picture: hold the last frame (no stretch)
        padded = folder / "clip_padded.mp4"
        media.run(["-i", str(clip), "-vf", f"tpad=stop_mode=clone:stop_duration={dur - cut['cut']['duration']:.2f}",
                   "-af", f"apad=pad_dur={dur - cut['cut']['duration']:.2f}", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(padded)])
        clip = padded
    out = folder / "short.mp4"
    try:
        r = render.render_short(folder, clip, pathlib.Path(nar["path"]), pathlib.Path(cap["ass"]), pathlib.Path(g["hook"]),
                                pathlib.Path(g["meter"]), g["meter_geo"], pathlib.Path(g["end_card"]), pathlib.Path(g["music"]),
                                tl["event_at"], dur, pr, out, info["width"], info["height"])
    except Exception as e:
        with session() as s:
            rj = s.get(RenderJob, rjid)
            rj.status, rj.log_tail = "failed", str(e)[-3000:]
        raise
    with session() as s:
        rj = s.get(RenderJob, rjid)
        rj.status, rj.seconds, rj.command, rj.log_tail = "done", r["seconds"], r["command"], r["log"]
        pc = s.scalar(select(ProductionCost).where(ProductionCost.project_id == pid))
        pc.render_seconds = (pc.render_seconds or 0) + r["seconds"]
        p = s.get(ClipProject, pid)
        _done(p, "render", {"path": str(out), "seconds": r["seconds"]})


def st_cover(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        frames = p.stages["analyze"]["output"]["frames"]
        sc = p.stages["commentary"]["output"]["script"]
    text = " ".join((sc.get("cover_text") or "CHAT EXPLODED").split()[:3])
    cv = render.cover(pathlib.Path(frames[1]), text, folder / "cover.jpg")
    th = render.thumbnail(pathlib.Path(frames[1]), text, folder / "thumbnail.jpg")
    with session() as s:
        _done(s.get(ClipProject, pid), "cover", {"cover": str(cv), "thumbnail": str(th), "text": text})


def st_metadata(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        sc = p.stages["commentary"]["output"]["script"]
        ev = p.stages["commentary"]["output"]["evidence"]
        d = rights.decide(s, ls)
        md = packaging.build_metadata(sc, ev, ls, c, d, d.attribution, original_graphics=False)
        (folder / "metadata.json").write_text(json.dumps(md, indent=1, ensure_ascii=False), encoding="utf-8")
        (folder / "description.txt").write_text(md["description"], encoding="utf-8")
        p.title = md["recommended_title"]
        _done(p, "metadata", md)


def st_qc(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        sc = p.stages["commentary"]["output"]["script"]
        d = rights.decide(s, ls)
        nar_dur = sum(ln["duration"] for ln in p.stages["narration"]["output"]["lines"])
        total = p.stages["timeline"]["output"]["clip_duration"]
        style = p.stages["captions"]["output"]["style"]
        is_sim = ls.is_simulation
        hooks = p.stages["commentary"]["output"].get("hookscore", [])
    out = pathlib.Path(folder / "short.mp4")
    reuse = rights.reuse_risk(d, True, max(0.0, 1 - nar_dur / max(total, 1)))
    q = packaging.qc(out, sc, d, reuse, style, simulation=bool(is_sim), hooks=hooks)
    sheet = render.contact_sheet(out, folder / "contact_sheet.png")
    with session() as s:
        p = s.get(ClipProject, pid)
        p.qc = q
        info = q["probe"]
        md = p.stages["metadata"]["output"]
        cv = p.stages["cover"]["output"]
        s.add(VideoExport(project_id=pid, path=str(out), sha256=sha256(out), width=info["width"] or 0, height=info["height"] or 0,
                          duration=info["duration"], video_codec=info["vcodec"] or "", audio_codec=info["acodec"] or "",
                          loudness_lufs=q["lufs"], cover_path=cv["cover"], thumb_path=cv["thumbnail"], metadata_json=md))
        pc = s.scalar(select(ProductionCost).where(ProductionCost.project_id == pid))
        pc.storage_bytes = sum(f.stat().st_size for f in pathlib.Path(p.folder).rglob("*") if f.is_file())
        _done(p, "qc", {**{k: v for k, v in q.items() if k != "probe"}, "contact_sheet": str(sheet)})


def st_review(pid):
    with session() as s:
        p, c, ls, folder = _ctx(s, pid)
        if not p.qc.get("passed"):
            p.status = "qc_failed"
            c.production_status = "in_production"
            notify(s, "Short failed QC", f"Project {pid}: {', '.join(p.qc.get('blocking', []))}", "warn", f"#studio/{pid}")
        else:
            p.status = "ready_for_approval"
            c.production_status = "ready_for_approval"
            notify(s, "Short ready for approval", p.title, "info", f"#studio/{pid}")
        _done(p, "review", {"status": p.status})
    events.publish("project", {"id": pid, "status": "ready"})


STAGE_FN = {"verify_rights": st_verify_rights, "retrieve_footage": st_retrieve, "analyze": st_analyze, "select_cuts": st_cuts,
            "commentary": st_commentary, "narration": st_narration, "captions": st_captions, "timeline": st_timeline,
            "graphics": st_graphics, "render": st_render, "cover": st_cover, "metadata": st_metadata, "qc": st_qc,
            "review": st_review}
