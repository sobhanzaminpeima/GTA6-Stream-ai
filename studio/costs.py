"""API usage, quotas and money: every paid call is checked against the budget first.

Prices are list prices used for estimates (actual invoices may differ):
- Anthropic claude-opus-5-5: $4 / MTok input, $20 / MTok output (skill table, 2026-11-03).
- ElevenLabs: credits per character; Creator plan ~ $0.30 per 1K characters overage-equivalent,
  configurable via STUDIO_ELEVENLABS_USD_PER_1K.
- YouTube Data API: free, 10,000 units/day default quota (upload = 1600 units in older docs;
  we track units, not dollars).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import automation
from .models import ApiUsage, ProductionCost

LLM_PRICES = {  # USD per million tokens (input, output)
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}
YOUTUBE_DAILY_QUOTA = 10_000


class BudgetExceeded(RuntimeError):
    pass


class PaidCallNotAllowed(RuntimeError):
    pass


def today() -> str:
    return dt.date.today().isoformat()


def record(s: Session, provider: str, operation: str, units: float, unit_type: str = "quota",
           cost_usd: float = 0.0, estimated: bool = False, project_id: int | None = None) -> None:
    s.add(ApiUsage(provider=provider, operation=operation, day=today(), units=units, unit_type=unit_type,
                   cost_usd=cost_usd, estimated=estimated, project_id=project_id))
    if project_id and cost_usd:
        pc = s.scalar(select(ProductionCost).where(ProductionCost.project_id == project_id))
        if pc is None:
            pc = ProductionCost(project_id=project_id, breakdown={})
            s.add(pc)
        pc.actual_usd = round((pc.actual_usd or 0) + cost_usd, 5)
        bd = dict(pc.breakdown or {})
        bd[provider] = round(bd.get(provider, 0) + cost_usd, 5)
        pc.breakdown = bd


def spent(s: Session, since: str) -> float:
    return float(s.scalar(select(func.coalesce(func.sum(ApiUsage.cost_usd), 0.0)).where(ApiUsage.day >= since)) or 0.0)


def quota_used(s: Session, provider: str = "youtube_data") -> float:
    return float(s.scalar(select(func.coalesce(func.sum(ApiUsage.units), 0.0)).where(
        ApiUsage.provider == provider, ApiUsage.day == today())) or 0.0)


def check_budget(s: Session, provider: str, estimate_usd: float) -> None:
    """Raise unless this paid request is allowed and fits the daily and monthly budgets."""
    a = automation(s)
    flag = {"anthropic": "allow_paid_llm", "elevenlabs": "allow_paid_tts"}.get(provider)
    if flag and not a.get(flag):
        raise PaidCallNotAllowed(f"paid {provider} calls are off (Automation > {flag}); using the local fallback")
    d = spent(s, today())
    m = spent(s, dt.date.today().replace(day=1).isoformat())
    if d + estimate_usd > float(a["daily_budget_usd"]):
        raise BudgetExceeded(f"daily budget ${a['daily_budget_usd']:.2f} would be exceeded (spent ${d:.2f})")
    if m + estimate_usd > float(a["monthly_budget_usd"]):
        raise BudgetExceeded(f"monthly budget ${a['monthly_budget_usd']:.2f} would be exceeded (spent ${m:.2f})")


def check_quota(s: Session, units: int, provider: str = "youtube_data") -> None:
    used = quota_used(s, provider)
    if used + units > YOUTUBE_DAILY_QUOTA * 0.95:
        raise BudgetExceeded(f"YouTube quota: {used:.0f}/{YOUTUBE_DAILY_QUOTA} units used today")


def llm_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = LLM_PRICES.get(model, (4.0, 20.0))
    return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout


def tts_cost(chars: int) -> float:
    return chars / 1000 * float(settings.get("STUDIO_ELEVENLABS_USD_PER_1K", "0.30"))


def estimate_short(script_chars: int = 650) -> dict:
    llm = llm_cost(settings.llm_model, 2500, 1500)
    tts = tts_cost(script_chars)
    return {"llm_usd": round(llm, 4), "tts_usd": round(tts, 4), "render": "local (free)",
            "total_usd": round(llm + tts, 4)}


def summary(s: Session) -> dict:
    a = automation(s)
    month = dt.date.today().replace(day=1).isoformat()
    rows = s.execute(select(ApiUsage.provider, ApiUsage.unit_type, func.sum(ApiUsage.units), func.sum(ApiUsage.cost_usd),
                            func.count()).where(ApiUsage.day >= month).group_by(ApiUsage.provider, ApiUsage.unit_type)).all()
    per_short = s.execute(select(func.avg(ProductionCost.actual_usd), func.avg(ProductionCost.estimated_usd),
                                 func.count())).one()
    return {
        "today_usd": round(spent(s, today()), 4), "month_usd": round(spent(s, month), 4),
        "daily_budget_usd": a["daily_budget_usd"], "monthly_budget_usd": a["monthly_budget_usd"],
        "youtube_quota_today": quota_used(s), "youtube_quota_limit": YOUTUBE_DAILY_QUOTA,
        "by_provider": [{"provider": p, "unit_type": u, "units": round(n or 0, 1), "usd": round(c or 0, 4), "calls": k}
                        for p, u, n, c, k in rows],
        "avg_actual_per_short": round(per_short[0] or 0, 4), "avg_estimated_per_short": round(per_short[1] or 0, 4),
        "shorts_costed": per_short[2], "estimate_next_short": estimate_short(),
        "paid_llm_enabled": a["allow_paid_llm"], "paid_tts_enabled": a["allow_paid_tts"],
    }
