"""Score a scraped candidate against a portfolio company and decide its state.

score = 0.75 * best name similarity + 0.25 * corroboration (same city, or the
candidate's name is the company's domain stem). An exact name alone lands at 0.75,
which is review, not auto-link: short names like "Pair" or "Cube" collide too often.
"""
import re

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from src.match.normalize import normalize

# Filers that are investment vehicles, not operating companies.
FUND_WORDS = re.compile(r"\b(fund|funds|l ?p|l l ?c a series|capital|partners|ventures|holdings|spv|"
                        r"series [a-z0-9]+ of|a series of|investors|master|feeder|ts )\b")


def similarity(a: str, b: str) -> dict:
    """Jaro-Winkler, token-set and plain ratio, each 0-1. Token-set alone would score
    'adonis' vs 'adonis capital partners' as a perfect match, so it gets the smallest weight."""
    jw = JaroWinkler.normalized_similarity(a, b)
    ts = fuzz.token_set_ratio(a, b) / 100
    ratio = fuzz.ratio(a, b) / 100
    return {"jaro_winkler": jw, "token_set": ts, "combined": 0.45 * jw + 0.25 * ts + 0.30 * ratio}


def domain_stem(domain: str | None) -> str | None:
    if not domain:
        return None
    host = domain.lower().removeprefix("www.")
    return re.sub(r"[^a-z0-9]", "", host.split(".")[0]) or None


# US cities in the portfolio table -> state, so "Brooklyn, NY" still backs "New York".
CITY_STATE = {
    "new york": "NY", "san francisco": "CA", "los angeles": "CA", "monterey": "CA", "austin": "TX",
    "denver": "CO", "boulder": "CO", "boston": "MA", "chicago": "IL", "miami": "FL", "detroit": "MI",
    "philadelphia": "PA", "pittsburgh": "PA", "seattle": "WA", "portland": "OR", "atlanta": "GA",
    "cleveland": "OH", "columbus": "OH", "cincinnati": "OH", "indianapolis": "IN", "kansas city": "MO",
    "st louis": "MO", "salt lake city": "UT", "phoenix": "AZ", "durham": "NC", "washington dc": "DC",
    "bozeman": "MT",
}


def same_city(ours: str | None, theirs: list[str]) -> float:
    """1.0 for the same city, 0.8 for the same US state (metro areas file from suburbs
    and boroughs), else 0."""
    if not ours:
        return 0.0
    city = normalize(ours)
    if any(normalize(loc.split(",")[0]) == city for loc in theirs if loc):
        return 1.0
    state = CITY_STATE.get(city)
    if state and any(loc.rsplit(",", 1)[-1].strip().upper() == state for loc in theirs if "," in loc):
        return 0.8
    return 0.0


def score_candidate(company_name: str, company_city: str | None, company_domain: str | None,
                    candidate_name: str, candidate_locations: list[str], legal_name: str | None = None) -> dict:
    """legal_name (from the company's own site) counts as a second name, and as
    corroboration: a filer matching the name the company itself prints is strong evidence."""
    if legal_name:
        a = score_candidate(company_name, company_city, company_domain, candidate_name, candidate_locations)
        b = score_candidate(legal_name, company_city, company_domain, candidate_name, candidate_locations)
        if b["reason"] is None and b["jaro_winkler"] >= 0.97 and b["token_set"] >= 0.97:
            b = {**b, "score": 1.0, "reason": "matches the legal name on the company's site"}
        return max(a, b, key=lambda m: m["score"])
    ours, theirs = normalize(company_name), normalize(candidate_name)
    # Check fund words before normalize(), which strips "LP".
    raw_theirs = " ".join(re.sub(r"[^a-z0-9 ]", " ", candidate_name.lower()).split())
    raw_ours = " ".join(re.sub(r"[^a-z0-9 ]", " ", company_name.lower()).split())
    if FUND_WORDS.search(raw_theirs) and not FUND_WORDS.search(raw_ours):
        return {"jaro_winkler": 0.0, "token_set": 0.0, "domain_match": 0, "city_match": 0,
                "score": 0.0, "reason": "filer is an investment vehicle"}

    by_name = similarity(ours, theirs)
    stem = domain_stem(company_domain)
    by_domain = similarity(stem, theirs.replace(" ", "")) if stem else {"combined": 0.0}
    # A domain only corroborates when it says something the name doesn't: paireyewear.com
    # backs "Pair Eyewear, Inc." for "Pair"; devo.co.uk adds nothing to "Devo, Inc.".
    informative = bool(stem) and stem != ours.replace(" ", "")
    domain_match = int(informative and by_domain["combined"] >= 0.97)
    city_match = same_city(company_city, candidate_locations)

    best = max(by_name["combined"], by_domain["combined"])
    score = 0.75 * best + 0.25 * max(domain_match, city_match)
    return {"jaro_winkler": round(by_name["jaro_winkler"], 4), "token_set": round(by_name["token_set"], 4),
            "domain_match": domain_match, "city_match": city_match, "score": round(min(score, 1.0), 4),
            "reason": None}


def state_for(score: float, auto_link: float, reject: float) -> str:
    if score >= auto_link:
        return "AUTO_LINKED"
    if score < reject:
        return "REJECTED"
    return "PENDING_REVIEW"
