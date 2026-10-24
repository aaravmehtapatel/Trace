"""SEC EDGAR Form D: full-text search for candidates, then the filing's own XML for amounts.

Fair access: a descriptive User-Agent with a contact email, and no more than ~8
requests a second (SEC's limit is 10).
"""
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as ET

from src.ingest.http import Fetcher

EFTS = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/primary_doc.xml"
SEARCH_FROM = "2018-01-01"  # the fund started investing in 2017-18


def fetcher(user_agent: str) -> Fetcher:
    return Fetcher("sec_edgar", {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                   concurrency=4, min_interval=0.125, timeout=20)


async def search(f: Fetcher, name: str) -> tuple[list[dict], str | None]:
    """Candidate filers for a company name, one per CIK, with every filing listed."""
    record, raw = await f.get(EFTS, params={"q": f'"{name}"', "forms": "D,D/A", "dateRange": "custom",
                                            "startdt": SEARCH_FROM}, ttl=7 * 24 * 3600)
    by_cik: dict[str, dict] = {}
    for hit in ((record or {}).get("body") or {}).get("hits", {}).get("hits", []):
        src = hit["_source"]
        adsh = hit["_id"].split(":")[0]
        for display, cik in zip(src.get("display_names", []), src.get("ciks", [])):
            c = by_cik.setdefault(cik, {"cik": cik, "name": display.split("(CIK")[0].strip(),
                                        "locations": set(), "filings": []})
            c["locations"].update(src.get("biz_locations") or [])
            c["filings"].append({"adsh": adsh, "form": src.get("form"), "file_date": src.get("file_date")})
    out = []
    for c in by_cik.values():
        c["locations"] = sorted(c["locations"])
        c["filings"].sort(key=lambda x: x["file_date"] or "", reverse=True)
        out.append(c)
    return out, str(raw) if raw else None


async def lookup(f: Fetcher, query: str) -> tuple[list[dict], str | None]:
    """EDGAR's company-name lookup (the search box's suggestions). Finds filers that
    full-text search misses, e.g. "Cube" -> "Cube Planning Inc."."""
    record, raw = await f.get(EFTS, params={"keysTyped": query}, ttl=7 * 24 * 3600)
    hits = (((record or {}).get("body") or {}).get("hits") or {}).get("hits") or []
    return [{"cik": str(h["_id"]).zfill(10), "name": h["_source"].get("entity", "")} for h in hits], str(raw) if raw else None


async def filer(f: Fetcher, cik: str) -> tuple[dict, str | None]:
    """A filer's own record: name, business and mailing city, and every Form D / D/A.
    Full-text search misses filings whose text doesn't repeat the name, so a linked
    filer's history always comes from here."""
    record, raw = await f.get(SUBMISSIONS.format(cik=int(cik)), ttl=24 * 3600)
    body = (record or {}).get("body") or {}
    recent = (body.get("filings") or {}).get("recent") or {}
    filings = [{"adsh": a, "form": form, "file_date": d}
               for a, form, d in zip(recent.get("accessionNumber", []), recent.get("form", []), recent.get("filingDate", []))
               if form in ("D", "D/A")]
    locations = sorted({f"{a.get('city', '').title()}, {a.get('stateOrCountry', '')}"
                        for a in (body.get("addresses") or {}).values() if a and a.get("city")})
    return {"cik": cik, "name": body.get("name", ""), "locations": locations,
            "filings": sorted(filings, key=lambda x: x["file_date"], reverse=True)}, str(raw) if raw else None


def _num(root, path: str) -> float | None:
    el = root.find(path)
    try:
        return float(el.text) if el is not None and el.text else None
    except ValueError:  # "Indefinite"
        return None


async def filing_detail(f: Fetcher, cik: str, adsh: str) -> tuple[dict, str | None]:
    url = ARCHIVE.format(cik=int(cik), adsh=adsh.replace("-", ""))
    record, raw = await f.get(url, ttl=365 * 24 * 3600, as_text=True)
    detail = {"url": url, "total_offering": None, "total_sold": None, "first_sale": None, "industry": None}
    body = (record or {}).get("body")
    if not body or (record or {}).get("status") != 200:
        return detail, str(raw) if raw else None
    try:
        root = ET.fromstring(body)
    except ParseError:
        return detail, str(raw) if raw else None
    detail["total_offering"] = _num(root, ".//offeringSalesAmounts/totalOfferingAmount")
    detail["total_sold"] = _num(root, ".//offeringSalesAmounts/totalAmountSold")
    el = root.find(".//typeOfFiling/dateOfFirstSale/value")
    detail["first_sale"] = el.text if el is not None else None
    el = root.find(".//industryGroup/industryGroupType")
    detail["industry"] = el.text if el is not None else None
    return detail, str(raw) if raw else None
