"""Async GET with an append-only raw store, so re-runs and the live demo don't burn rate limits.

Every response is written to data/raw/<source>/<YYYY-MM-DD>/<key>.json.gz and never
overwritten. A cached copy younger than `ttl` is reused. GitHub calls send the cached
ETag as If-None-Match: a 304 costs no rate-limit quota.
"""
import asyncio
import gzip
import hashlib
import json
import time
from datetime import date
from pathlib import Path

import httpx

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def _key(url: str, params: dict | None) -> str:
    return hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()


def _latest(source: str, key: str) -> Path | None:
    hits = sorted((RAW_DIR / source).glob(f"*/{key}.json.gz"))
    return hits[-1] if hits else None


def _write(source: str, key: str, record: dict) -> Path:
    path = RAW_DIR / source / date.today().isoformat() / f"{key}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with gzip.open(path, "wt") as f:
            json.dump(record, f)
    return path


def _read(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


class Fetcher:
    """One per source. Limits concurrency and spacing so SEC/GitHub fair-use rules hold."""

    def __init__(self, source: str, headers: dict, concurrency: int = 5,
                 min_interval: float = 0.0, timeout: float = 10.0):
        self.source = source
        # IPv4 only: httpx doesn't fall back from an unreachable IPv6 address, so
        # sites with AAAA records timed out here while curl loaded them fine.
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
        self.client = httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True,
                                        transport=transport)
        self.sem = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def _space(self):
        if not self.min_interval:
            return
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def get(self, url: str, params: dict | None = None, ttl: int = 24 * 3600,
                  as_text: bool = False, etag: bool = False) -> tuple[dict | None, Path | None]:
        """Returns ({'status', 'url', 'body', ...}, raw_path). body is JSON or text."""
        key = _key(url, params)
        cached = _latest(self.source, key)
        if cached and time.time() - cached.stat().st_mtime < ttl:
            return _read(cached), cached

        headers = {}
        if etag and cached:
            old = _read(cached)
            if old.get("etag"):
                headers["If-None-Match"] = old["etag"]

        async with self.sem:
            for attempt in range(3):
                await self._space()
                try:
                    resp = await self.client.get(url, params=params, headers=headers)
                except httpx.HTTPError as e:
                    return {"status": None, "url": url, "error": type(e).__name__, "body": None}, None
                if resp.status_code in (403, 429, 502, 503) and attempt < 2:  # rate limits, crt.sh overload
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                break

        if resp.status_code == 304 and cached:
            record = _read(cached)
        else:
            body = resp.text if as_text else (resp.json() if resp.status_code == 200 else None)
            record = {"status": resp.status_code, "url": url, "final_url": str(resp.url),
                      "etag": resp.headers.get("etag"), "fetched": time.time(), "body": body}
        return record, _write(self.source, key, record)
