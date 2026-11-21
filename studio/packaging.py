"""YouTube metadata (titles, description, hashtags, tags, playlist, pinned comment) and
render quality checks. Titles must match what the clip shows; GTA 5 footage is never
titled as GTA 6; no misleading clickbait.
"""
from __future__ import annotations

import pathlib

from . import media, subtitles
from .commentary import GAME_LABEL, title_lint

PLAYLISTS = {"gta_vi_commentary": "GTA 6 Explained", "confirmed_gameplay": "GTA 6 Moments",
             "gta_v_gameplay": "GTA Moments (GTA 5)", "trailer_reaction": "GTA 6 Trailer Breakdowns",
             "fan_concept": "GTA Fan Concepts", "unverified": "GTA Moments"}


def titles_for(game: str, reaction: str | None, mult: float, structure: str) -> list[str]:
    r = (reaction or "").upper()
    t = [
        f"The Chat Couldn't Believe This {game} Moment",
        f"Chat Went {mult:.0f}x Faster at This {game} Moment" if mult >= 2 else f"Chat Lit Up at This {game} Moment",
        f"Nobody Expected This {game} Moment",
        f"Why Chat Typed \"{r.title() or 'Clip It'}\" During This {game} Stream",
        f"This {game} Moment Explained in 30 Seconds" if structure == "EDUCATIONAL" else f"What Just Happened in This {game} Stream?",
    ]
    return [x[:100] for x in t]


def build_metadata(script: dict, ev: dict, ls, cand, rights_dec, attribution: str | None, original_graphics: bool) -> dict:
    game = ev["game_label"]
    if ev["chat"] is None:  # no chat evidence: no chat claims in titles
        titles = [f"Watch the Exact Second This {game} Clip Changes", f"The Biggest Moment in This {game} Clip",
                  f"Don't Skip This {game} Moment", f"This {game} Moment in 30 Seconds", f"Did You Catch This {game} Moment?"]
    else:
        mult = ev["chat"]["multiple_of_baseline"] or 1
        titles = titles_for(game, cand.dominant_reaction, mult, script["structure"])
    lint = title_lint(titles, script.get("cover_text"))
    scored = sorted(lint, key=lambda r: (r["score"] or 0), reverse=True)
    best = scored[0]["title"] if scored else titles[0]
    credit = []
    if rights_dec.state != "OWNED":
        credit.append(f"Footage: {ls.channel_name} ({ls.source_url}), used with permission"
                      + (f" - {attribution}" if attribution else "") + ".")
    else:
        credit.append("Footage: GTA VI Central's own video" + (" (original graphics)" if original_graphics else "") + ".")
    if ev["content_type"] == "gta_v_gameplay":
        credit.append("Note: this is GTA 5 gameplay, not GTA 6.")
    demo = ["DEMO RENDER - chat activity in this video is SIMULATED. Not for publishing.", ""] if ev["stream"].get("simulation") else []
    desc = "\n".join([
        *demo,
        script["hooks"][0],
        "",
        script["context_summary"] if len(script["context_summary"]) < 400 else script["context_summary"][:397] + "...",
        "",
        "Original commentary and analysis by GTA VI Central. Narration: AI voice.",
        *credit,
        "Not affiliated with Rockstar Games or Take-Two Interactive.",
        "",
        "#GTA6 #GTAVI #Shorts" if game.startswith("GTA 6") else "#GTA #Gaming #Shorts",
    ])
    tags = ["GTA 6", "GTA VI", "Grand Theft Auto VI", "GTA 6 moments", "GTA 6 stream", "gaming shorts"] if game.startswith("GTA 6") \
        else ["GTA", "GTA 5", "Grand Theft Auto V", "GTA moments", "gaming shorts"]
    return {
        "title_options": titles, "title_lint": lint, "recommended_title": best,
        "description": desc,
        "hashtags": ["#GTA6", "#GTAVI", "#Shorts"] if game.startswith("GTA 6") else ["#GTA", "#GTA5", "#Shorts"],
        "tags": tags, "playlist_suggestion": PLAYLISTS.get(ev["content_type"], "GTA Moments"),
        "pinned_comment": script["outro_lines"][-1] if script.get("outro_lines") else "What would you have done?",
        "related_video_idea": f"A longer breakdown: the {cand.dominant_reaction or 'top'} moments of {ls.channel_name}'s streams, compared.",
        "cover_text": script.get("cover_text", "")[:40],
        "category_id": "20",  # Gaming
        "made_for_kids": False,
        "contains_synthetic_media": True,  # AI narration voice -> disclose in Studio when asked
        "default_privacy": "private",
    }


def qc(video: pathlib.Path, script: dict, rights_dec, reuse: dict, caption_style: dict | None = None,
       simulation: bool = False, hooks: list[dict] | None = None) -> dict:
    info = media.probe(video)
    lufs = media.loudness(video)
    blacks = media.black_frames(video)
    checks = {
        "resolution_1080x1920": info.get("width") == 1080 and info.get("height") == 1920,
        "codec_h264": info.get("vcodec") == "h264",
        "audio_aac": info.get("acodec") == "aac",
        "duration_15_60s": 15 <= info.get("duration", 0) <= 60,
        "duration_25_45s_channel_rule": 25 <= info.get("duration", 0) <= 45,
        "loudness_-14_lufs": lufs is not None and abs(lufs + 14) <= 1.5,
        "no_black_frames": not blacks,
        "captions_in_safe_zone": subtitles.safe_zone_ok(caption_style)["ok"],
        "rights_allow_youtube": rights_dec.can_publish_youtube,
        "claims_have_evidence": all(c.get("evidence") for c in script.get("claims", [])),
        "original_commentary_present": bool(script.get("intro_lines") and script.get("outro_lines")),
        "not_a_simulation": not simulation,
        "hook_score_70_channel_rule": max([h.get("score") or 0 for h in (hooks or [])] or [0]) >= 70,
    }
    blocking = [k for k in ("resolution_1080x1920", "codec_h264", "audio_aac", "duration_15_60s", "rights_allow_youtube",
                            "claims_have_evidence", "original_commentary_present") if not checks[k]]
    return {"checks": checks, "passed": not blocking, "blocking": blocking, "probe": info, "lufs": lufs,
            "black_frames": blacks, "reuse_risk": reuse,
            "warnings": [k for k, v in checks.items() if not v and k not in blocking]}
