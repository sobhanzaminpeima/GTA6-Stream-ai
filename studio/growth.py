"""Weekly growth report from REAL analytics only, with sample-size guards.

Never claims causation. Comparisons need >= MIN_N videos per group; below that,
findings are labelled HYPOTHESIS (RULES.md: no conclusions from < ~5 comparable videos).
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from sqlalchemy import select

from .db import session
from .models import AnalyticsSnapshot, GrowthRecommendation, PublishedVideo, ViralCandidate

MIN_N = 5


def _latest_metrics(s) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for snap in s.scalars(select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.day)):
        out[snap.youtube_video_id] = snap.metrics
    return out


def _bucket_duration(d) -> str:
    if d is None:
        return "unknown"
    return "<25s" if d < 25 else "25-35s" if d < 35 else "35-45s" if d <= 45 else ">45s"


def compare(videos: list[dict], key: str, metric: str = "views") -> list[dict]:
    groups: dict[str, list[float]] = defaultdict(list)
    for v in videos:
        if v["metrics"].get(metric) is not None:
            groups[str(v["attributes"].get(key) if key != "duration_bucket" else _bucket_duration(v["attributes"].get("clip_duration")))].append(float(v["metrics"][metric]))
    rows = []
    for g, xs in groups.items():
        rows.append({"group": g, "n": len(xs), "median": statistics.median(xs), "mean": round(statistics.fmean(xs), 1),
                     "confidence": "enough data" if len(xs) >= MIN_N else f"HYPOTHESIS (n={len(xs)} < {MIN_N})"})
    return sorted(rows, key=lambda r: r["median"], reverse=True)


def weekly_report(week: str | None = None) -> dict:
    week = week or dt.date.today().strftime("%G-W%V")
    with session() as s:
        metrics = _latest_metrics(s)
        vids = []
        for pv in s.scalars(select(PublishedVideo)):
            vids.append({"id": pv.youtube_video_id, "title": pv.title, "attributes": pv.attributes or {},
                         "metrics": metrics.get(pv.youtube_video_id, {})})
        with_data = [v for v in vids if v["metrics"]]
        cands = s.scalars(select(ViralCandidate)).all()
        dismissed = [c for c in cands if c.production_status == "dismissed"]
        report = {"week": week, "published_tracked": len(vids), "with_analytics": len(with_data),
                  "data_note": None if len(with_data) >= MIN_N else
                  f"Only {len(with_data)} Shorts with analytics: every comparison below is a hypothesis, not a finding.",
                  "top_views": sorted(with_data, key=lambda v: v["metrics"].get("views", 0), reverse=True)[:5],
                  "top_subscribers": sorted(with_data, key=lambda v: v["metrics"].get("subscribersGained", 0), reverse=True)[:5],
                  "by_duration": compare(with_data, "duration_bucket"),
                  "by_hook_type": compare(with_data, "hook_type"),
                  "by_source": compare(with_data, "stream_source"),
                  "by_preset": compare(with_data, "editing_preset"),
                  "by_topic_subs": compare(with_data, "topic", "subscribersGained"),
                  "weak": sorted(with_data, key=lambda v: v["metrics"].get("averageViewPercentage", 100))[:3],
                  "candidate_funnel": {"detected": len(cands), "dismissed": len(dismissed),
                                       "produced": sum(c.production_status not in ("new", "saved", "dismissed") for c in cands)},
                  "unavailable_metrics": ["Shorts feed 'viewed vs swiped away' is Studio-only (not in the Analytics API)"]}
        recs = []
        if len(with_data) < MIN_N:
            recs.append("Keep the rhythm (~1 Short/day) and vary one attribute at a time so groups become comparable.")
        else:
            for name, rows in (("length", report["by_duration"]), ("hook type", report["by_hook_type"]), ("preset", report["by_preset"])):
                good = [r for r in rows if r["n"] >= MIN_N]
                if len(good) >= 2:
                    recs.append(f"{name}: '{good[0]['group']}' has the highest median views ({good[0]['median']:.0f}, n={good[0]['n']}) "
                                f"vs '{good[-1]['group']}' ({good[-1]['median']:.0f}). Correlation, not proof: test it again.")
        top = sorted([c for c in cands if c.production_status in ("new", "saved")], key=lambda c: c.overall_score, reverse=True)[:3]
        for c in top:
            recs.append(f"Candidate #{c.id} ({c.overall_score:.0f}, {c.tier}, rights {c.rights_state}) is unproduced"
                        + (" - request footage permission first." if c.rights_state not in ("OWNED", "EXPLICITLY_LICENSED", "AUTHORIZED_FOR_CLIPPING") else "."))
        report["recommendations"] = recs
        s.add(GrowthRecommendation(week=week, report=report))
        return report
