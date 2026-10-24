"""Akta (Wokelo) licensed company data: dated news events and monthly headcount.

Headcount is the signal funds usually pull from LinkedIn; Akta licenses it, so we don't
scrape. Needs AKTA_API_KEY in census/.env. Endpoints the plan doesn't include return 403
and are skipped (pay-as-you-go has News Signals but not Headcount Trends).
"""
import os
from collections import Counter

from src.ingest.http import Fetcher

API = "https://api.akta.pro/api/v1"
NEGATIVE = ("layoff", "lay off", "laid off", "shut down", "shuts down", "shutting down", "winds down", "wind down",
            "bankrupt", "insolven", "ceases operations", "closes its doors", "acquired by", "acquires")


def available() -> bool:
    return bool(os.environ.get("AKTA_API_KEY"))


def fetcher(per_minute: int = 80) -> Fetcher:
    """Subscription plans allow 100 requests a minute overall and 10 a minute for headcount;
    pass per_minute=9 for the headcount fetcher. (Pay-as-you-go: 25 a minute, no headcount.)"""
    return Fetcher("akta", {"x-api-key": os.environ.get("AKTA_API_KEY", ""), "Accept": "application/json"},
                   concurrency=3, min_interval=60 / per_minute, timeout=40)


async def news(f: Fetcher, domain: str, limit: int = 50) -> tuple[dict, str | None]:
    """Month -> articles mentioning the company, plus the few most useful items."""
    record, raw = await f.get(f"{API}/news/", params={"company": f"https://{domain}", "limit": limit}, ttl=24 * 3600)
    status = (record or {}).get("status")
    items = []
    for a in (((record or {}).get("body") or {}).get("data") or []):
        title = a.get("title") or ""
        tags = [t.get("name") for t in a.get("tags") or [] if t.get("is_primary")] or [t.get("name") for t in a.get("tags") or []][:1]
        items.append({"date": (a.get("published_date") or "")[:10], "title": title, "url": a.get("url"),
                      "publisher": a.get("publisher"), "sentiment": a.get("sentiment"), "tag": tags[0] if tags else None,
                      "warning": any(w in (title + " " + (a.get("ai_summary") or "")).lower() for w in NEGATIVE)})
    items.sort(key=lambda x: x["date"], reverse=True)
    months = Counter(i["date"][:7] for i in items if i["date"])
    return {"ok": status == 200, "status": status, "total": ((record or {}).get("body") or {}).get("total"),
            "months": dict(months), "items": items[:8]}, str(raw) if raw else None


async def resolve(f: Fetcher, domain: str) -> tuple[str | None, str | None]:
    """Akta uuid for a domain via the free company search, or None. Only an exact website match
    counts: a domain can map to several entities, and a near match is a different company."""
    record, raw = await f.get(f"{API}/company/search/", params={"query": domain}, ttl=7 * 24 * 3600)
    for c in (((record or {}).get("body") or {}).get("data") or []):
        site = (c.get("website") or "").lower().split("//")[-1].removeprefix("www.").strip("/")
        if site == domain.removeprefix("www."):
            return c.get("uuid"), str(raw) if raw else None
    return None, str(raw) if raw else None


async def headcount(f: Fetcher, domain: str, f_slow: Fetcher | None = None) -> tuple[dict, str | None]:
    """f_slow carries the headcount call itself, under that endpoint's tighter limit."""
    uuid, raw0 = await resolve(f, domain)
    if not uuid:
        return {"ok": False, "status": 404, "reason": "no exact website match in Akta"}, raw0
    record, raw = await (f_slow or f).get(f"{API}/company/headcount-trends/", params={"company": uuid}, ttl=7 * 24 * 3600)
    status = (record or {}).get("status")
    d = ((record or {}).get("body") or {}).get("data") or {}
    growth = {g.get("month_difference"): g.get("change_percentage") for g in d.get("growth_periods") or []}
    return {"ok": status == 200 and bool(d), "status": status, "total": d.get("total_employees"),
            "growth_6": growth.get(6), "growth_12": growth.get(12), "growth_24": growth.get(24),
            "monthly": {m["date"][:7]: m.get("employee_count") for m in d.get("headcount_monthly") or [] if m.get("date")},
            "functions": {fn.get("name"): {"count": fn.get("employee_count"),
                                           "growth_6": next((g.get("change_percentage") for g in fn.get("growth_periods") or []
                                                             if g.get("month_difference") == 6), None)}
                          for fn in (d.get("headcount_by_function") or [])[:6]},
            "linkedin": d.get("linkedin_username"), "akta_uuid": uuid}, ";".join(str(r) for r in (raw0, raw) if r)
