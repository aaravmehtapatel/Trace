"""Public GitHub activity for orgs the company's own website links to.

Uses the GitHub CLI's token when present (5,000 calls/hour instead of 60) and
sends cached ETags, so unchanged orgs cost nothing on re-runs.
"""
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from src.ingest.http import Fetcher

API = "https://api.github.com"


def _token() -> str | None:
    if shutil.which("gh"):
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None


def fetcher() -> Fetcher:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Fetcher("github", headers, concurrency=6, timeout=15)


async def activity(f: Fetcher, login: str, window_days: int) -> tuple[dict, list[str]]:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    repos, raw1 = await f.get(f"{API}/users/{login}/repos", params={"sort": "pushed", "per_page": 30},
                              ttl=12 * 3600, etag=True)
    events, raw2 = await f.get(f"{API}/users/{login}/events/public", params={"per_page": 100},
                               ttl=12 * 3600, etag=True)
    own = [r for r in ((repos or {}).get("body") or []) if isinstance(r, dict) and not r.get("fork")]
    evs = [e for e in ((events or {}).get("body") or []) if isinstance(e, dict)]
    recent = [e for e in evs if e.get("created_at", "") >= since.isoformat()[:19]]
    last_push = max((r.get("pushed_at") or "" for r in own), default="") or None
    releases = [e for e in recent if e.get("type") == "ReleaseEvent"]
    # Month -> activity: public events (last ~90 days) plus each repo's last push.
    months: dict[str, int] = {}
    for stamp in [e.get("created_at", "") for e in evs] + [r.get("pushed_at") or "" for r in own]:
        if stamp:
            months[stamp[:7]] = months.get(stamp[:7], 0) + 1
    return {
        "login": login,
        "found": (repos or {}).get("status") == 200,
        "public_repos": len(own),
        "last_push": last_push[:10] if last_push else None,
        "events_in_window": len(recent),
        "pushes_in_window": sum(1 for e in recent if e.get("type") == "PushEvent"),
        "releases_in_window": len(releases),
        "top_repo": own[0]["full_name"] if own else None,
        "months": months,
        "url": f"https://github.com/{login}",
    }, [str(r) for r in (raw1, raw2) if r]
