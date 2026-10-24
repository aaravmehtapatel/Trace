"""Website, TLS and sitemap probe. Also harvests the GitHub and job-board links the
other ingesters follow, so we never guess a company's GitHub org or careers page."""
import asyncio
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from src.ingest.http import Fetcher

PARKED = re.compile(r"domain (is|may be) for sale|\bfor sale \| spaceship|future home of|website is for sale|coming soon|buy this domain|hugedomains|sedo\.com|dan\.com|afternic|"
                    r"parked free|this domain has expired|godaddy\.com/domainsearch", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
OG_SITE = re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', re.I)
HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d\d)", re.I)
# "© 2025 Arrissa Labs Inc." -> the legal name a Form D is filed under, which is often
# not the brand (Switchboard files as Arrissa Labs).
LEGAL = re.compile(r"(?:©|&copy;|&#169;|copyright)\s*(?:\d{4}\s*(?:[-–]\s*\d{4})?\s*)?,?\s*"
                   r"([A-Z0-9][A-Za-z0-9&.,'’ -]{1,60}?\b(?:Inc|LLC|Ltd|Limited|Corp|Corporation|PBC|GmbH|Co)\b\.?)", re.I)
LASTMOD = re.compile(r"<lastmod>\s*(\d{4}-\d\d-\d\d)", re.I)
ATS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([\w-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I),
}
GITHUB = re.compile(r"github\.com/([A-Za-z0-9-]+)(?:/|$)")
NOT_ORGS = {"sponsors", "orgs", "features", "about", "login", "join", "marketplace", "topics", "apps"}


def fetcher(timeout: float, concurrency: int) -> Fetcher:
    # A browser-shaped UA: bot walls (Cloudflare, Vercel) block a bare crawler string,
    # which would wrongly read as "site not live".
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/130.0 Safari/537.36 PortfolioCensus/0.1")
    return Fetcher("web", {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml",
                           "Accept-Language": "en-US,en;q=0.9"},
                   concurrency=concurrency, timeout=timeout)


async def cert_not_after(host: str, timeout: float) -> str | None:
    """TLS certificate expiry (notAfter) as an ISO date, or None if the handshake fails."""
    ctx = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host,
                                                                   family=socket.AF_INET),
                                           timeout)
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    cert = writer.get_extra_info("peercert") or {}
    writer.close()
    if "notAfter" not in cert:
        return None
    return datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc).date().isoformat()


def legal_name(html: str) -> str | None:
    m = LEGAL.search(re.sub(r"<[^>]+>", " ", html))
    return " ".join(m.group(1).split()).rstrip(".,") if m else None


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def harvest(html: str, base: str) -> dict:
    links = [urljoin(base, h) for h in HREF.findall(html)]
    joined = " ".join(links)
    github = sorted({m for m in GITHUB.findall(joined) if m.lower() not in NOT_ORGS})
    ats = {kind: sorted(set(rx.findall(joined))) for kind, rx in ATS.items()}
    careers = next((u for u in links if re.search(r"/(careers|jobs|join-us|work-with-us)\b", u, re.I)
                    and host_of(u) == host_of(base)), None)
    return {"github": github, "ats": {k: v for k, v in ats.items() if v}, "careers_url": careers}


async def probe(f: Fetcher, domain: str, timeout: float) -> tuple[dict, list[str]]:
    """Everything the footprint score and bucket rules need, with the raw dumps used."""
    raws = []
    home, raw = await f.get(f"https://{domain}/", as_text=True, ttl=20 * 3600)
    raws.append(raw)
    html = (home or {}).get("body") or ""
    status = (home or {}).get("status")
    final = (home or {}).get("final_url") or ""
    title = TITLE.search(html)
    og = OG_SITE.search(html)
    out = {
        "domain": domain,
        "status": status,
        "error": (home or {}).get("error"),
        "final_url": final,
        "final_host": host_of(final) if final else None,
        "redirected_off_domain": bool(final) and host_of(final) != domain.removeprefix("www.")
                                 and not host_of(final).endswith("." + domain.removeprefix("www.")),
        "title": " ".join(title.group(1).split())[:200] if title else None,
        "site_name": og.group(1) if og else None,
        "parked": bool(PARKED.search(html[:50000])),
        "copyright_year": max(map(int, COPYRIGHT.findall(html)), default=None),
        "cert_not_after": await cert_not_after(domain, timeout),
        "sitemap_lastmod": None,
        "legal_name": legal_name(html),
        **harvest(html, final or f"https://{domain}/"),
    }
    if status == 200 and not out["parked"]:
        sitemap, raw = await f.get(f"https://{domain}/sitemap.xml", as_text=True, ttl=20 * 3600)
        raws.append(raw)
        dates = LASTMOD.findall((sitemap or {}).get("body") or "") if (sitemap or {}).get("status") == 200 else []
        today = datetime.now(timezone.utc).date()
        valid = sorted({d for d in dates if d <= today.isoformat()})
        # Sitemaps generated on request stamp every page with today's date; that says
        # nothing about the company, so a single fresh date is ignored.
        out["sitemap_autodated"] = len(valid) == 1 and valid[0] >= (today - timedelta(days=2)).isoformat()
        out["sitemap_lastmod"] = valid[-1] if valid and not out["sitemap_autodated"] else None
        if out["careers_url"] and not out["ats"]:
            careers, raw = await f.get(out["careers_url"], as_text=True, ttl=20 * 3600)
            raws.append(raw)
            more = harvest((careers or {}).get("body") or "", out["careers_url"])
            out["ats"] = more["ats"]
            out["legal_name"] = out["legal_name"] or legal_name((careers or {}).get("body") or "")
            out["github"] = sorted(set(out["github"]) | set(more["github"]))
    # Visible text plus meta descriptions; scripts and styles would crowd out the copy.
    metas = " ".join(re.findall(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', html, re.I))
    text = re.sub(r"<(script|style|svg|noscript)\b.*?</\1>", " ", html, flags=re.S | re.I)
    out["body_sample"] = metas + " " + re.sub(r"<[^>]+>", " ", text)[:80000]
    return out, [str(r) for r in raws if r]
