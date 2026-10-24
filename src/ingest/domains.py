"""Find each company's website. Candidates come from Claude (live API when a key
works, else the hints file Claude wrote at build time) and from name patterns; the
site itself has to confirm it. A proposal is never trusted on its own.

Verification: the company name (or its domain stem) must appear in the page title or
og:site_name for an auto-link, or in the page body for review.
"""
import json
import os
import re
from pathlib import Path

import anthropic

from src.match.normalize import normalize
from src.match.resolve import state_for

MODEL = "claude-sonnet-5-5"
HINTS = Path(__file__).resolve().parents[2] / "config" / "domain_hints.json"
BATCH = 25

PROMPT = """For each startup below, list up to 3 likely website domains, most likely first.
Use what you know; if unsure, give your best guesses from the name (e.g. getX.com, X.ai, tryX.com).
Return only JSON: {{"<id>": ["domain.com", ...], ...}}

{rows}"""


def patterns(name: str) -> list[str]:
    """Common startup domain shapes, tried only after the proposals fail."""
    compact = normalize(name).replace(" ", "")
    if not compact:
        return []
    return [f"{compact}.com", f"{compact}.ai", f"{compact}.io", f"{compact}.co",
            f"get{compact}.com", f"try{compact}.com", f"join{compact}.com", f"{compact}hq.com"]


def from_hints(companies: list[dict]) -> dict[int, list[str]]:
    hints = json.loads(HINTS.read_text()) if HINTS.exists() else {}
    return {c["id"]: hints[c["raw_name"]] for c in companies if c["raw_name"] in hints}


def propose(companies: list[dict]) -> dict[int, list[str]]:
    """Live Claude proposals first, then the hints file; the hints alone if the API is unavailable."""
    hints = from_hints(companies)
    try:
        live = _propose_live(companies)
    except (anthropic.AuthenticationError, anthropic.APIConnectionError, anthropic.PermissionDeniedError):
        return hints
    return {c["id"]: list(dict.fromkeys(live.get(c["id"], []) + hints.get(c["id"], []))) for c in companies}


def _client() -> anthropic.Anthropic:
    """CENSUS_ANTHROPIC_API_KEY (from census/.env) talks to the public API directly, so a
    shell that sets its own ANTHROPIC_* variables for other tools doesn't interfere."""
    key = os.environ.get("CENSUS_ANTHROPIC_API_KEY")
    if key:
        return anthropic.Anthropic(api_key=key, auth_token="", base_url="https://api.anthropic.com")
    return anthropic.Anthropic()


def _propose_live(companies: list[dict]) -> dict[int, list[str]]:
    client = _client()
    out: dict[int, list[str]] = {}
    for i in range(0, len(companies), BATCH):
        chunk = companies[i:i + BATCH]
        rows = "\n".join(json.dumps({"id": c["id"], "name": c["raw_name"], "city": c["city"],
                                     "what_it_does": (c["description"] or "")[:240]}) for c in chunk)
        msg = client.messages.create(model=MODEL, max_tokens=4000,
                                     messages=[{"role": "user", "content": PROMPT.format(rows=rows)}])
        text = "".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            continue
        for k, v in json.loads(m.group(0)).items():
            doms = [d.lower().strip().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")
                    for d in v if isinstance(d, str)]
            out[int(k)] = [d for d in doms if "." in d and " " not in d][:3]
    return out


STOP = set("about across their there which while where these those other offers provides platform company "
           "solutions solution designed helps using based through services service including business businesses "
           "people users enables simple simplifies digital online first world every".split())


def keywords(description: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]{5,}", (description or "").lower()) if w not in STOP}


def verify(company_name: str, probe: dict, auto_link: float, reject: float,
           description: str | None = None, guessed: bool = False) -> dict:
    """Score a probed domain as the company's site. A pattern-guessed domain (headway.com
    for Headway) must also talk about what the fund says the company does, or it goes to
    review: common names collide with unrelated companies."""
    name = normalize(company_name)
    compact = name.replace(" ", "")
    stem = re.sub(r"[^a-z0-9]", "", probe["domain"].split(".")[0])
    head = normalize(" ".join(filter(None, [probe.get("title"), probe.get("site_name")])))
    body = normalize(probe.get("body_sample") or "")

    exact_stem = stem == compact or stem == "".join(normalize(company_name).split()[:1])
    if probe.get("status") != 200 or probe.get("parked"):
        score, where = 0.0, "site not live"
    elif probe.get("redirected_off_domain") and compact not in probe.get("final_host", "").replace("-", ""):
        # mryum.com -> meandu.com: often a rebrand, so a person decides.
        score, where = (0.8 if exact_stem else 0.3), f"redirects to {probe.get('final_host')}"
    elif re.search(rf"\b{re.escape(name)}\b", head) or (len(compact) >= 5 and compact in head.replace(" ", "")):
        score, where = 0.97 if (compact in stem or stem in compact) else 0.93, "name in page title"
    elif exact_stem and (re.search(rf"\b{re.escape(stem)}\b", head) or re.search(rf"\b{re.escape(name)}\b", body)):
        score, where = 0.93, "domain is the name, and the name is on the page"
    elif re.search(rf"\b{re.escape(name)}\b", body):
        score, where = 0.8, "name in page body only"
    elif exact_stem:
        score, where = 0.75, "domain is the name, but the page doesn't say it"
    else:
        score, where = 0.5, "name not on page"
    if score >= auto_link and description:
        # Calibrated on hand-checked sites: wrong namesakes (revive.com, otis.ai) share at most
        # ~25% of the description's distinctive words. Pattern guesses must clear that bar;
        # proposed domains only need two shared words, since JS-heavy real sites show little text.
        kw = keywords(description) - set(name.split()) - {compact}
        overlap = kw & set(body.split())
        ratio = len(overlap) / max(1, min(len(kw), 12))
        if (guessed and (len(overlap) < 4 or ratio < 0.33)) or (not guessed and len(overlap) < 2):
            score, where = 0.85, where + f"; page shares {len(overlap)} words with the fund's description"
    return {"score": score, "reason": where, "state": state_for(score, auto_link, reject)}
