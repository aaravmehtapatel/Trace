"""Open roles from the public job-board APIs a company's site links to (no auth needed)."""
from datetime import datetime, timezone

from src.ingest.http import Fetcher

BOARDS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs", "https://boards.greenhouse.io/{t}"),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", "https://jobs.lever.co/{t}"),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", "https://jobs.ashbyhq.com/{t}"),
}


def fetcher() -> Fetcher:
    return Fetcher("jobs", {"User-Agent": "Mozilla/5.0 (compatible; PortfolioCensus/0.1)"}, concurrency=6)


def _parse(kind: str, body) -> list[dict]:
    """(title, updated date) per posting, whatever the board's shape."""
    if kind == "greenhouse":
        return [{"title": j.get("title"), "updated": (j.get("updated_at") or "")[:10]} for j in (body or {}).get("jobs", [])]
    if kind == "lever":
        return [{"title": j.get("text"),
                 "updated": datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).date().isoformat()
                 if j.get("createdAt") else ""} for j in (body or []) if isinstance(j, dict)]
    if kind == "ashby":
        return [{"title": j.get("title"), "updated": (j.get("publishedAt") or "")[:10]} for j in (body or {}).get("jobs", [])]
    return []


async def openings(f: Fetcher, ats: dict[str, list[str]]) -> tuple[dict | None, list[str]]:
    for kind, tokens in ats.items():
        for token in tokens:
            api, page = BOARDS[kind]
            record, raw = await f.get(api.format(t=token), ttl=12 * 3600)
            if (record or {}).get("status") != 200:
                continue
            jobs = _parse(kind, record["body"])
            latest = max((j["updated"] for j in jobs if j["updated"]), default=None)
            return {"board": kind, "token": token, "url": page.format(t=token), "open_roles": len(jobs),
                    "latest_posting": latest, "titles": [j["title"] for j in jobs[:8]]}, [str(raw)]
    return None, []
