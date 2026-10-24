"""Dated public history for a domain, so a trend exists on the first run instead of after
months of snapshots. All free and unauthenticated:

  Web Archive CDX       each time the site's content changed (collapsed by page digest)
  Certificate logs      the month each subdomain first got a certificate (app., api., ...)
  DNS over HTTPS        whether the domain still receives email (MX records)
  RDAP                  domain registration, expiry and last change
  Tranco                rank among the top 1M sites, a free traffic proxy
"""
import csv
import io
import time
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx

from src.ingest.http import RAW_DIR, Fetcher

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
HISTORY_MONTHS = 24


def fetcher() -> Fetcher:
    return Fetcher("history", {"User-Agent": "PortfolioCensus/0.1 (portfolio research)"}, concurrency=12, timeout=30)


def months_ago(n: int) -> str:
    d = date.today().replace(day=1)
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
    return d.strftime("%Y-%m")


async def _cdx(f: Fetcher, url: str) -> tuple[list, str | None, bool]:
    record, raw = await f.get("http://web.archive.org/cdx/search/cdx",
                              params={"url": url, "output": "json", "collapse": "digest",
                                      "from": months_ago(HISTORY_MONTHS).replace("-", ""), "fl": "timestamp,statuscode"},
                              ttl=7 * 24 * 3600)
    return ((record or {}).get("body") or [])[1:], str(raw) if raw else None, (record or {}).get("status") == 200


async def web_changes(f: Fetcher, domain: str) -> tuple[dict, str | None]:
    """Month -> distinct homepage versions the Web Archive captured. When the apex mostly
    redirects (example.com -> www.example.com), the www homepage is read too, so the real
    history isn't lost. (A whole-domain query is better data but too slow at this scale.)"""
    rows, raw, ok = await _cdx(f, domain)
    raws = [raw]
    if rows and sum(1 for r in rows if len(r) > 1 and r[1].startswith("3")) >= len(rows) / 2:
        more, raw2, ok2 = await _cdx(f, f"www.{domain}")
        rows, ok = rows + more, ok or ok2
        raws.append(raw2)
    months = Counter(f"{r[0][:4]}-{r[0][4:6]}" for r in rows if len(r) > 1 and r[1] in ("200", "-"))
    return {"months": dict(months), "captures": sum(months.values()), "ok": ok}, ";".join(r for r in raws if r) or None


async def new_subdomains(f: Fetcher, domain: str) -> tuple[dict, str | None]:
    """Month -> hostnames seen for the first time in certificate-transparency logs. A cert
    renewal for an existing name doesn't count; a new name (app., api., docs.) is new work."""
    record, raw = await f.get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}, ttl=7 * 24 * 3600)
    first: dict[str, str] = {}
    for cert in ((record or {}).get("body") or []):
        for name in str(cert.get("name_value", "")).lower().split("\n"):
            name = name.strip().lstrip("*.")
            if name.endswith(domain) and cert.get("not_before"):
                first[name] = min(first.get(name, "9999"), cert["not_before"][:7])
    return {"months": dict(Counter(first.values())), "names": len(first),
            "ok": (record or {}).get("status") == 200}, str(raw) if raw else None


async def mail(f: Fetcher, domain: str) -> tuple[dict, str | None]:
    record, raw = await f.get("https://dns.google/resolve", params={"name": domain, "type": "MX"}, ttl=24 * 3600)
    answers = [a.get("data", "") for a in ((record or {}).get("body") or {}).get("Answer", []) if a.get("type") == 15]
    provider = next((p for key, p in (("google", "Google Workspace"), ("outlook", "Microsoft 365"),
                                      ("zoho", "Zoho"), ("protonmail", "Proton")) if any(key in a for a in answers)), None)
    return {"has_mx": bool(answers), "mx": answers[:3], "provider": provider,
            "ok": (record or {}).get("status") == 200}, str(raw) if raw else None


async def registration(f: Fetcher, domain: str) -> tuple[dict, str | None]:
    record, raw = await f.get(f"https://rdap.org/domain/{domain}", ttl=24 * 3600)
    events = {e.get("eventAction"): (e.get("eventDate") or "")[:10]
              for e in ((record or {}).get("body") or {}).get("events", []) if isinstance(e, dict)}
    return {"registered": events.get("registration"), "expires": events.get("expiration"),
            "last_changed": events.get("last changed"), "ok": (record or {}).get("status") == 200}, str(raw) if raw else None


def tranco_ranks() -> dict[str, int]:
    """Today's Tranco top-1M list, cached in data/raw/tranco/<date>.csv."""
    path = RAW_DIR / "tranco" / f"{date.today().isoformat()}.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(TRANCO_URL, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            path.write_bytes(z.read(z.namelist()[0]))
    ranks = {}
    with path.open() as fh:
        for rank, host in csv.reader(fh):
            ranks[host] = int(rank)
    return ranks
