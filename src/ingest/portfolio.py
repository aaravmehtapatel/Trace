"""Pull the fund's own public portfolio table (everywhere.vc, robots.txt allows it)."""
import html
import re

import httpx

PORTFOLIO_URL = "https://www.everywhere.vc/"
PAGE_PARAM = "1e6e22b2_page"  # Webflow pagination key for the portfolio table

_ITEM_START = '<div data-table="" class="portfolio-list">'
_FIELD = {
    "name": re.compile(r'data-table-name=""[^>]*>(.*?)</h4>', re.S),
    "city": re.compile(r'data-table-city="">(.*?)</div>', re.S),
    "description": re.compile(r'data-table-desc=""[^>]*>(.*?)</div>', re.S),
    "sector": re.compile(r'data-table-label="">(.*?)</div>', re.S),
}
_LINK = re.compile(r'href="(https?://[^"]+)"')


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse(page_html: str) -> list[dict]:
    rows = []
    for item in page_html.split(_ITEM_START)[1:]:
        row = {}
        for key, pattern in _FIELD.items():
            m = pattern.search(item)
            row[key] = _text(m.group(1)) if m else None
        links = [u for u in _LINK.findall(item) if "everywhere.vc" not in u]
        row["link"] = links[0] if links else None
        if row["name"]:
            rows.append(row)
    return rows


def fetch_all(max_pages: int = 60) -> list[dict]:
    seen, out = set(), []
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (portfolio census research)"},
                      follow_redirects=True, timeout=20) as client:
        for page in range(1, max_pages + 1):
            params = {PAGE_PARAM: page} if page > 1 else None
            rows = parse(client.get(PORTFOLIO_URL, params=params).text)
            new = [r for r in rows if r["name"] not in seen]
            if not new:
                break
            for r in new:
                seen.add(r["name"])
            out.extend(new)
    return out
