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


def fetcher() -> Fetcher:
    # Pay-as-you-go allows 25 requests a minute across all endpoints; stay under it.
    return Fetcher("akta", {"x-api-key": os.environ.get("AKTA_API_KEY", ""), "Accept": "application/json"},
                   concurrency=2, min_interval=2.6, timeout=40)


async def news(f: Fetcher, domain: str, limit: int = 50) -> tuple[dict, str | None]:
    """Month -> articles mentioning the company, plus the few most useful items."""
    record, raw = await f.get(f"{API}/news", params={"company": f"https://{domain}", "limit": limit}, ttl=24 * 3600)
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


async def headcount(f: Fetcher, domain: str) -> tuple[dict, str | None]:
    uuid, raw0 = await resolve(f, domain)
    if not uuid:
        return {"ok": False, "status": 404, "reason": "no exact website match in Akta"}, raw0
    record, raw = await f.get(f"{API}/company/headcount-trends", params={"company": uuid}, ttl=7 * 24 * 3600)
    status = (record or {}).get("status")
    d = ((record or {}).get("body") or {}).get("data") or {}
    growth = {g.get("month_difference"): g.get("change_percentage") for g in d.get("growth_periods") or []}
    return {"ok": status == 200 and bool(d), "status": status, "total": d.get("total_employees"),
            "growth_6": growth.get(6), "growth_12": growth.get(12), "growth_24": growth.get(24),
            "monthly": {m["date"][:7]: m.get("employee_count") for m in d.get("headcount_monthly") or [] if m.get("date")},
            "linkedin": d.get("linkedin_username"), "akta_uuid": uuid}, ";".join(r for r in (raw0, raw) if r)
