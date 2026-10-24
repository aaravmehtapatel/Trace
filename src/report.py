"""Exports: census.json (machine audit log), census.md (human audit log), census.html (the report).

Every company's bucket is written with the rule that fired, the dated signals behind it
and their source links, so any line can be checked by hand.
"""
import json
from datetime import date
from pathlib import Path

from src import calibrate, score
from src.config import Config

TEMPLATE = Path(__file__).with_name("report_template.html")
ORDER = ["Active", "Fading", "Quiet", "No Public Activity", "Unresolved"]


def _latest(conn, entity_id: int, kind: str) -> dict | None:
    row = conn.execute("""SELECT payload, source_url FROM signal_observations WHERE entity_id = ? AND signal_type = ?
                          ORDER BY observed_at DESC, id DESC LIMIT 1""", (entity_id, kind)).fetchone()
    return {**json.loads(row["payload"]), "source_url": row["source_url"]} if row else None


def collect(conn, cfg: Config) -> dict:
    score_date = conn.execute("SELECT MAX(score_date) FROM portfolio_daily_scores").fetchone()[0]
    if not score_date:
        raise SystemExit("No scores yet: run `score` first.")
    companies = []
    for r in conn.execute("""SELECT e.*, s.bucket, s.last_activity_date, s.composite_score, s.form_d_score,
                                    s.engineering_score, s.hiring_score, s.footprint_score, s.evidence
                             FROM entities e JOIN portfolio_daily_scores s ON s.entity_id = e.id AND s.score_date = ?
                             ORDER BY e.raw_name COLLATE NOCASE""", (score_date,)):
        ev = json.loads(r["evidence"])
        fp, eng, hire = (_latest(conn, r["id"], k) for k in ("footprint", "engineering", "hiring"))
        filings = [json.loads(x["payload"]) for x in conn.execute(
            """SELECT o.payload FROM signal_observations o JOIN match_candidates m ON m.entity_id = o.entity_id
                 AND m.source = 'sec_edgar' AND m.candidate_key = json_extract(o.payload, '$.cik')
                 AND m.state IN ('AUTO_LINKED', 'CONFIRMED')
               WHERE o.entity_id = ? AND o.signal_type = 'form_d' GROUP BY json_extract(o.payload, '$.adsh')
               ORDER BY o.event_date DESC""", (r["id"],))]
        pending = conn.execute("""SELECT source, candidate_name, candidate_key, score FROM match_candidates
                                  WHERE entity_id = ? AND state = 'PENDING_REVIEW' ORDER BY score DESC""",
                               (r["id"],)).fetchall()
        companies.append({
            "id": r["id"], "name": r["raw_name"], "city": r["city"], "sector": r["sector"],
            "description": r["description"], "domain": r["main_domain"], "legal_name": r["legal_name"],
            "bucket": r["bucket"], "last_activity": r["last_activity_date"], "rule": ev["rule"],
            "composite": r["composite_score"],
            "scores": {"form_d": r["form_d_score"], "engineering": r["engineering_score"],
                       "hiring": r["hiring_score"], "footprint": r["footprint_score"]},
            "signals": ev["signals"],
            "trend": ev.get("trend"), "badges": ev.get("badges", []),
            "mail": ev.get("mail"), "registration": ev.get("registration"), "subdomains": ev.get("subdomains"),
            "headcount": ev.get("headcount"), "news": ev.get("news", []),
            "filings": [{"form": f["form"], "date": f["file_date"], "filer": f["filer"], "sold": f.get("total_sold"),
                         "offering": f.get("total_offering"), "url": f["url"]} for f in filings],
            "site": {k: fp.get(k) for k in ("status", "final_host", "title", "cert_not_after", "sitemap_lastmod",
                                            "parked", "redirected_off_domain", "error")} if fp else None,
            "github": {k: eng.get(k) for k in ("login", "url", "last_push", "events_in_window", "public_repos")} if eng else None,
            "hiring": {k: hire.get(k) for k in ("board", "url", "open_roles", "latest_posting")} if hire else None,
            "pending": [dict(p) for p in pending][:5],
        })
    counts = {b: sum(1 for c in companies if c["bucket"] == b) for b in ORDER}
    form_d_year = sum(1 for c in companies if any(b["kind"] == "form_d" and b.get("recent") for b in c["badges"]))
    raised_news = sum(1 for c in companies if any(b["label"].startswith("Raised, per news") and b.get("recent") for b in c["badges"]))
    # Portfolio headcount now and a year ago, over companies Akta has a full year for.
    year_ago = score.month_keys(13, date.fromisoformat(score_date))[0]
    with_hc = [c for c in companies if (c.get("headcount") or {}).get("monthly")
               and (c["headcount"].get("total") or 0) <= score.MAX_PLAUSIBLE_HEADCOUNT]
    now_total = sum(c["headcount"]["total"] or 0 for c in with_hc if year_ago in c["headcount"]["monthly"])
    then_total = sum(c["headcount"]["monthly"][year_ago] or 0 for c in with_hc if year_ago in c["headcount"]["monthly"])
    people = {"companies": len(with_hc), "now": now_total, "year_ago": then_total,
              "growth_pct": round(100 * (now_total - then_total) / then_total) if then_total else None,
              "growing": sum(1 for c in with_hc if (c["headcount"].get("growth_6") or 0) >= calibrate.GROWING),
              "shrinking": sum(1 for c in with_hc if (c["headcount"].get("growth_6") or 0) <= calibrate.SHRINKING)}
    linked = conn.execute("SELECT COUNT(*) FROM match_candidates WHERE state IN ('AUTO_LINKED', 'CONFIRMED')").fetchone()[0]
    reviewed = conn.execute("SELECT COUNT(*) FROM match_candidates WHERE reviewed_at IS NOT NULL").fetchone()[0]
    observations = conn.execute("SELECT COUNT(*) FROM signal_observations").fetchone()[0]
    return {
        "fund": cfg.fund_name, "score_date": score_date, "generated": date.today().isoformat(),
        "counts": counts, "total": len(companies), "companies": companies, "calls": calls(companies),
        "form_d_year": form_d_year, "raised_news": raised_news, "people": people, "accuracy": calibrate.calibrate(conn, cfg),
        "method": {"weights": cfg.weights, "auto_link": cfg.auto_link, "reject": cfg.reject,
                   "window_days": cfg.window_days, "active_days": score.ACTIVE_DAYS, "fading_ratio": score.FADING_RATIO,
                   "trend_months": [score.TREND_RECENT, score.TREND_PRIOR],
                   "observations": observations, "linked_matches": linked, "human_reviewed": reviewed,
                   "pending_review": sum(len(c["pending"]) > 0 for c in companies)},
    }


def _has(c: dict, kind: str) -> bool:
    return any(b["kind"] == kind for b in c["badges"])


def calls(companies: list[dict], limit: int = 10) -> list[dict]:
    """Who a partner should contact this week, each with the reason and the question to ask.
    Recently dark first (a mark or a write-off may be due), then fading, then rising."""
    # Redirects to another company (likely exits) before sites that simply went dark.
    # Exits first (acquisitions in the news, then redirects to another company), then sites
    # that simply went dark.
    gone = sorted((c for c in companies if c["bucket"] == "No Public Activity"),
                  key=lambda c: (not c["rule"].startswith("Acquired"), not _has(c, "redirect"),
                                 not (c.get("headcount") or {}).get("total"), c["name"]))
    # Headcount-backed fades first, steepest drop first; website-only fades are weaker evidence.
    hc_fading = sorted((c for c in companies if c["bucket"] == "Fading" and (c.get("headcount") or {}).get("growth_6") is not None),
                       key=lambda c: c["headcount"]["growth_6"])
    web_fading = [c for c in companies if c["bucket"] == "Fading" and c not in hc_fading]
    fading = hc_fading + web_fading[:1]
    rising = sorted((c for c in companies if c["bucket"] == "Active" and
                     (_has(c, "rising") or any(b["kind"] == "form_d" and b.get("recent") for b in c["badges"]))),
                    key=lambda c: c["composite"], reverse=True)
    out = []
    for c in gone[:3]:
        redirect = _has(c, "redirect")
        out.append({"id": c["id"], "kind": "gone", "reason": c["rule"],
                    "ask": "Confirm the terms and what the fund received, then update the mark."
                    if c["rule"].startswith("Acquired") else
                    "Was this an acquisition? Confirm what happened to the fund's shares and update the mark."
                    if redirect else "Is the company still operating? If not, start the write-off."})
    for c in fading[:4]:
        warn = next((n for n in c["news"] if n.get("warning")), None)
        fns = ((c.get("headcount") or {}).get("functions") or {})
        eng = fns.get("Engineering", {})
        detail = f"; engineering {eng['growth_6']:+}%" if eng.get("growth_6") is not None else ""
        out.append({"id": c["id"], "kind": "fading",
                    "reason": c["rule"] + detail + (f". In the news: {warn['title']}" if warn else ""),
                    "ask": "Who left, and what changed: the team, the product focus, or runway?"
                    if c.get("headcount") else "What changed this quarter: the team, the product focus, or runway?"})
    rising.sort(key=lambda c: ((c.get("headcount") or {}).get("growth_12") or 0), reverse=True)
    for c in rising[:limit - len(out)]:
        filed = next((b for b in c["badges"] if b["kind"] == "form_d" and b.get("recent")), None)
        hc = c.get("headcount") or {}
        grew = f"Headcount {hc['growth_12']:+}% in 12 months, to {hc['total']}" if hc.get("growth_12") else None
        out.append({"id": c["id"], "kind": "rising",
                    "reason": "; ".join(x for x in (grew, f"{filed['label']} filed" if filed else None) if x)
                    or "Public activity is up sharply",
                    "ask": "Is a round open, and does the fund have pro rata?" if filed
                    else "Momentum is up. Are they raising, and what do they need from us?"})
    return out[:limit]


def _label(c: dict) -> str:
    if c["bucket"] == "No Public Activity":
        return f"No Public Activity Since {c['last_activity']}" if c["last_activity"] else "No Public Activity"
    return c["bucket"]


def markdown(data: dict) -> str:
    lines = [f"# {data['fund']}: portfolio census, {data['score_date']}", "",
             f"{data['total']} companies. Buckets come from dated public signals; each line links its evidence.", ""]
    for b in ORDER:
        group = [c for c in data["companies"] if c["bucket"] == b]
        lines += [f"## {b} ({len(group)})", ""]
        for c in group:
            lines.append(f"- **{c['name']}** ({c['domain'] or 'no verified site'}): {_label(c)}. Rule: {c['rule']}.")
            for s in c["signals"][:4]:
                lines.append(f"  - {s['date']}: {s['what']} ([source]({s['url']}), observation {s['observation_id']})")
        lines.append("")
    m = data["method"]
    lines += ["## Method", "",
              f"- Match thresholds: auto-link at {m['auto_link']}, reject below {m['reject']}, review in between.",
              f"- Active means a dated signal within {m['active_days']} days; fading means the last "
              f"{m['trend_months'][0]} months fell below {m['fading_ratio']:.0%} of the {m['trend_months'][1]} before.",
              f"- Ranking weights: {', '.join(f'{k} {v}' for k, v in m['weights'].items())}.",
              f"- {m['observations']} observations, {m['linked_matches']} linked matches, "
              f"{m['human_reviewed']} decided by a person, {m['pending_review']} companies with matches awaiting review."]
    return "\n".join(lines) + "\n"


def write_all(data: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"census-{data['score_date']}"
    j, md, html = out_dir / f"{stem}.json", out_dir / f"{stem}.md", out_dir / f"{stem}.html"
    j.write_text(json.dumps(data, indent=1))
    md.write_text(markdown(data))
    payload = json.dumps(data).replace("</", "<\\/")
    html.write_text(TEMPLATE.read_text().replace("/*__CENSUS_DATA__*/null", payload))
    return [j, md, html]
