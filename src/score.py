"""Statuses come from the activity trend; Form D is a badge, not a status.

Two kinds of evidence:
  history signals   dated records of the past that don't favour recent months: Web Archive
                    content changes, first-seen subdomains in certificate logs, Form D filings.
                    Only these drive the trend, so the comparison is fair.
  current signals   things that only show the present: open job postings, latest GitHub push,
                    sitemap dates. They prove a company is active now but can't show a decline.

Rules, first match wins:
  Unresolved          no verified website, so no status is given
  No Public Activity  site down, parked or now another company's; domain expired; or no
                      email and no dated signal in 12 months. Dated by the last signal.
  Fading              history in the last 3 months below 35% of the 9 months before, with
                      enough history to judge and no current signal to contradict it
  Active              any dated signal in the last 90 days
  Quiet               otherwise
The weighted composite only ranks companies within a status.
"""
import json
import math
from datetime import date, timedelta

from src.config import Config
from src.match.normalize import normalize

ACTIVE_DAYS = 90
TREND_RECENT, TREND_PRIOR = 3, 9          # months
FADING_RATIO, MIN_PRIOR_EVENTS = 0.35, 4
RISING_RATIO, MIN_RECENT_EVENTS = 2.0, 3
# A drop must be unlikely by chance given what we'd expect to see. Public history for
# pre-seed companies is sparse, so 0.15 (an 85% call) is the honest bar; the record shows
# the exact chance so nobody mistakes it for certainty.
P_VALUE, MIN_EXPECTED = 0.15, 2.5
CAPS = {"web": 3, "certs": 3, "form_d": 2, "news": 3}   # per month, so one noisy source can't dominate
HEADCOUNT_DROP, HEADCOUNT_GROWTH = -15, 25   # % change over 6 and 12 months
CHART_MONTHS = 18


def month_keys(n: int, today: date) -> list[str]:
    """The last n months, oldest first, ending with the current month."""
    keys, d = [], today.replace(day=1)
    for _ in range(n):
        keys.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return keys[::-1]


def _days_since(d: str | None, today: date) -> int | None:
    if not d:
        return None
    try:
        return (today - date.fromisoformat(d[:10])).days
    except ValueError:
        return None


def _recency(days: int | None, horizon: int) -> float:
    return 0.0 if days is None or days < 0 else max(0.0, 1 - days / horizon)


def site_stem(host: str | None) -> str:
    """Registrable name of a host: fora.northeastern.edu -> northeastern, prophero.com.au -> prophero."""
    labels = (host or "").lower().split(".")
    if len(labels) > 2 and labels[-2] in ("co", "com", "org", "net", "ac"):
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


def moved_not_gone(company: str, p: dict) -> bool:
    """A redirect to the company's own new domain (edsights.io -> edsights.com) is a move,
    not a disappearance. A redirect to a site that doesn't carry the name (aerodome.com ->
    flocksafety.com) is what we flag."""
    compact = normalize(company).replace(" ", "")
    stem = site_stem(p.get("final_host")).replace("-", "")
    return bool(compact) and (compact in stem or (len(stem) >= 4 and stem in compact))


def latest(conn, entity_id: int, kind: str) -> dict | None:
    row = conn.execute("""SELECT id, payload, event_date, source_url, observed_at FROM signal_observations
                          WHERE entity_id = ? AND signal_type = ? ORDER BY observed_at DESC, id DESC LIMIT 1""",
                       (entity_id, kind)).fetchone()
    return {**dict(row), "payload": json.loads(row["payload"])} if row else None


def filings(conn, entity_id: int) -> list[dict]:
    """Form D observations from filers that are linked now (a later review can unlink one)."""
    rows = conn.execute("""SELECT o.id, o.payload, o.event_date, o.source_url FROM signal_observations o
                           JOIN match_candidates m ON m.entity_id = o.entity_id AND m.source = 'sec_edgar'
                             AND m.candidate_key = json_extract(o.payload, '$.cik')
                             AND m.state IN ('AUTO_LINKED', 'CONFIRMED')
                           WHERE o.entity_id = ? AND o.signal_type = 'form_d'
                           ORDER BY o.event_date DESC, o.id DESC""", (entity_id,)).fetchall()
    seen, out = set(), []
    for r in rows:
        p = json.loads(r["payload"])
        if p["adsh"] not in seen:
            seen.add(p["adsh"])
            out.append({**dict(r), "payload": p})
    return out


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam)."""
    term = total = math.exp(-lam)
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return min(total, 1.0)


def indexed_through(web_rows: list[dict], today: date, threshold: float = 0.7) -> date:
    """Latest month the Web Archive has indexed for most of the portfolio: the first month,
    counting back from now, where the share of companies with a captured change is at least
    70% of the share a year earlier. The trend window ends there."""
    keys = month_keys(18, today)
    if not web_rows:
        return today
    share = {m: sum(1 for r in web_rows if r.get(m)) / len(web_rows) for m in keys}
    base = sum(share[m] for m in keys[:9]) / 9 or 1.0
    for m in reversed(keys):
        if share[m] >= threshold * base:
            return date.fromisoformat(m + "-15")
    return today


def lag_factors(series_by_company: dict[str, list[dict]], today: date) -> dict[str, dict[str, float]]:
    """Per source, how much of a normal month each recent month shows across the whole portfolio.

    The Web Archive indexes recent captures late: in the Everywhere run the share of
    companies with any captured change falls from ~55 a month to 14-28 in the latest months.
    Judging each company against that portfolio baseline keeps index lag from reading as
    decline. A factor of 0.3 means a recent month shows 30% of what a normal month does."""
    keys = month_keys(TREND_RECENT + TREND_PRIOR, today)   # `today` here is the window end
    prior, recent = keys[:TREND_PRIOR], keys[TREND_PRIOR:]
    out = {}
    for src, rows in series_by_company.items():
        if not rows:
            out[src] = {m: 1.0 for m in recent}
            continue
        share = {m: sum(1 for r in rows if r.get(m)) / len(rows) for m in keys}
        base = sum(share[m] for m in prior) / len(prior) or 1.0
        out[src] = {m: max(0.05, min(1.0, share[m] / base)) for m in recent}
    return out


def timeline(web: dict | None, certs: dict | None, fds: list[dict], today: date, news: dict | None = None,
             lag: dict[str, dict[str, float]] | None = None, end: date | None = None) -> dict:
    """Monthly history-signal counts (capped per source) for the chart and the trend. The
    chart runs to today; the trend window ends at `end`, the last well-indexed month."""
    keys = month_keys(max(CHART_MONTHS, TREND_RECENT + TREND_PRIOR) + 12, today)
    fd_months: dict[str, int] = {}
    for f in fds:
        if f["event_date"]:
            fd_months[f["event_date"][:7]] = fd_months.get(f["event_date"][:7], 0) + 1
    series = {"web": (web or {}).get("payload", {}).get("months", {}),
              "certs": (certs or {}).get("payload", {}).get("months", {}),
              "form_d": fd_months,
              "news": (news or {}).get("payload", {}).get("months", {})}
    # Archive versions are counted on a log scale (1, 2-3, 4-7 -> 1, 2, 3): a big site with
    # 400 page versions a month shouldn't look 400 times more alive than a small one.
    def level(src, n):
        return min(CAPS[src], math.ceil(math.log2(n + 1))) if src == "web" else min(n, CAPS[src])
    months = [{"month": k, **{src: level(src, series[src].get(k, 0)) for src in series}} for k in keys]
    for m in months:
        m["total"] = m["web"] + m["certs"] + m["form_d"] + m["news"]
    end_key = (end or today).strftime("%Y-%m")
    upto = [m for m in months if m["month"] <= end_key]
    window = upto[-(TREND_RECENT + TREND_PRIOR):]
    prior_rows, recent_rows = window[:TREND_PRIOR], window[TREND_PRIOR:]
    recent = sum(m["total"] for m in recent_rows)
    prior = sum(m["total"] for m in prior_rows)
    rate_recent, rate_prior = recent / TREND_RECENT, prior / TREND_PRIOR
    # Expected recent activity if nothing changed: each source's prior monthly rate, scaled by
    # how complete that source's recent months are across the portfolio.
    lag = lag or {}
    expected = 0.0
    for src in series:
        src_rate = sum(m[src] for m in prior_rows) / TREND_PRIOR
        expected += sum(src_rate * lag.get(src, {}).get(m["month"], 1.0) for m in recent_rows)
    p_drop = poisson_cdf(recent, expected) if expected > 0 else 1.0
    p_rise = 1 - poisson_cdf(recent - 1, max(expected, 0.5)) if recent > 0 else 1.0
    have_history = bool(web) or bool(certs) or bool(news)
    return {"months": months[-CHART_MONTHS:], "recent": recent, "prior": prior,
            "window_end": end_key, "window_start": window[0]["month"] if window else None,
            "rate_recent": round(rate_recent, 2), "rate_prior": round(rate_prior, 2),
            "expected_recent": round(expected, 2), "p_drop": round(p_drop, 4),
            "fading": have_history and prior >= MIN_PRIOR_EVENTS and expected >= MIN_EXPECTED
                      and recent < FADING_RATIO * expected and p_drop < P_VALUE,
            "rising": have_history and recent >= MIN_RECENT_EVENTS and recent >= RISING_RATIO * max(expected, 1.0)
                      and p_rise < P_VALUE}


def score_entity(conn, cfg: Config, e: dict, today: date, lag: dict | None = None, end: date | None = None) -> dict:
    fp, eng, hire = (latest(conn, e["id"], k) for k in ("footprint", "engineering", "hiring"))
    web, certs, mail, reg, traffic, news, hc = (latest(conn, e["id"], k) for k in
                                               ("web_history", "cert_history", "mail", "domain_reg", "traffic", "news", "headcount"))
    fds = filings(conn, e["id"])
    tl = timeline(web, certs, fds, today, news, lag, end)
    hcp = (hc or {}).get("payload") or {}
    headcount_falling = hcp.get("growth_6") is not None and hcp["growth_6"] <= HEADCOUNT_DROP
    warnings = [i for i in ((news or {}).get("payload") or {}).get("items", []) if i.get("warning")]
    w = cfg.weights

    # Ranking scores, 0-1 (spec weights). They order companies within a status.
    fd_days = _days_since(fds[0]["event_date"], today) if fds else None
    sold = (fds[0]["payload"].get("total_sold") or 0) if fds else 0
    s_form_d = _recency(fd_days, 730) * (0.7 + 0.3 * min(1, math.log10(sold + 1) / 7))
    s_eng = 0.0
    if eng:
        p = eng["payload"]
        s_eng = 0.6 * min(1, p["events_in_window"] / 30) + 0.4 * _recency(_days_since(p["last_push"], today), cfg.window_days * 3)
    s_hire = 0.0
    if hire:
        p = hire["payload"]
        s_hire = 0.6 * min(1, p["open_roles"] / 10) + 0.4 * _recency(_days_since(p["latest_posting"], today), ACTIVE_DAYS)
    s_fp, live, moved = 0.0, False, False
    if fp:
        p = fp["payload"]
        moved = bool(p.get("redirected_off_domain")) and moved_not_gone(e["raw_name"], p)
        live = p.get("status") == 200 and not p.get("parked") and (not p.get("redirected_off_domain") or moved)
        cert_days = -_days_since(p.get("cert_not_after"), today) if p.get("cert_not_after") else None
        s_fp = (0.4 if live else 0) + (0.1 if cert_days and cert_days > 14 else 0) \
            + 0.2 * min(1, tl["rate_recent"] / 2) + 0.3 * _recency(_days_since(p.get("sitemap_lastmod"), today), ACTIVE_DAYS)
    composite = w["form_d"] * s_form_d + w["engineering"] * s_eng + w["hiring"] * s_hire + w["footprint"] * s_fp

    # Every dated signal, newest first: the evidence trail.
    dated = [(f["event_date"], f"Form {f['payload']['form']} filed", f["id"], f["source_url"]) for f in fds]
    if eng and eng["payload"].get("last_push"):
        dated.append((eng["payload"]["last_push"], f"Last GitHub push ({eng['payload']['login']})", eng["id"], eng["source_url"]))
    if hire and hire["payload"].get("latest_posting"):
        dated.append((hire["payload"]["latest_posting"], f"Job posted ({hire['payload']['open_roles']} open)", hire["id"], hire["source_url"]))
    if fp and fp["payload"].get("sitemap_lastmod"):
        dated.append((fp["payload"]["sitemap_lastmod"], "Website updated (sitemap)", fp["id"], fp["source_url"]))
    if web:
        changed = sorted(web["payload"].get("months", {}))
        if changed:
            dated.append((changed[-1] + "-15", "Homepage changed (Web Archive)", web["id"],
                          f"https://web.archive.org/web/*/{e['main_domain']}"))
    if certs:
        new = sorted(certs["payload"].get("months", {}))
        if new:
            dated.append((new[-1] + "-15", "New subdomain certified", certs["id"],
                          f"https://crt.sh/?q=%25.{e['main_domain']}"))
    for item in (((news or {}).get("payload") or {}).get("items") or [])[:3]:
        if item.get("date"):
            dated.append((item["date"], f"In the news: {item['title'][:90]}", news["id"], item.get("url")))
    dated = sorted((d for d in dated if d[0]), reverse=True)
    last_activity = dated[0][0] if dated else None
    last_days = _days_since(last_activity, today)

    # Current signals that contradict a decline.
    hiring_days = _days_since((hire or {}).get("payload", {}).get("latest_posting"), today)
    push_days = _days_since((eng or {}).get("payload", {}).get("last_push"), today)
    hiring_now = hiring_days is not None and hiring_days <= 60
    site_days = _days_since((fp or {}).get("payload", {}).get("sitemap_lastmod"), today)
    updating_now = site_days is not None and site_days <= 60
    shipping_now = push_days is not None and push_days <= 30
    raised_now = fd_days is not None and fd_days <= 180
    expires = (reg or {}).get("payload", {}).get("expires")
    expired = bool(expires and expires < today.isoformat())
    no_mail = bool(mail and not mail["payload"].get("has_mx"))

    if not e["main_domain"]:
        bucket, rule = "Unresolved", "No verified website yet"
    elif not live or expired or (no_mail and (last_days is None or last_days > 365)):
        if expired:
            why = f"domain registration expired {expires}"
        elif fp and fp["payload"].get("parked"):
            why = "website is parked"
        elif fp and fp["payload"].get("redirected_off_domain") and not moved:
            why = f"website now redirects to {fp['payload'].get('final_host')}, often an acquisition or rebrand"
        elif fp and not live:
            why = f"website returned {fp['payload'].get('status') or fp['payload'].get('error')}"
        else:
            why = "no email records and no dated signal in a year"
        bucket, rule = "No Public Activity", why[0].upper() + why[1:]
    elif headcount_falling and not raised_now:
        bucket = "Fading"
        rule = f"Headcount down {abs(hcp['growth_6'])}% in 6 months, to {hcp.get('total')} (Akta)"
    elif tl["fading"] and not hcp and not (hiring_now or shipping_now or raised_now or updating_now):
        # Website history alone is weak evidence: AscendArc looked like this while its headcount
        # grew 33%. It only marks a company fading when there's no headcount to check against.
        bucket = "Fading"
        rule = (f"Possible slowdown, website evidence only: {tl['recent']} dated changes in the {TREND_RECENT} months "
                f"to {tl['window_end']}, where about {tl['expected_recent']:.0f} were expected (chance: "
                f"{max(tl['p_drop'], 0.001):.1%}). No headcount data to confirm")
    elif hcp.get("growth_6") is not None and hcp["growth_6"] >= 10:
        bucket, rule = "Active", f"Headcount up {hcp['growth_6']}% in 6 months, to {hcp.get('total')} (Akta)"
    elif last_days is not None and last_days <= ACTIVE_DAYS:
        bucket, rule = "Active", f"{dated[0][1]} {last_activity}"
    else:
        bucket = "Quiet"
        rule = f"Site live; last dated signal {last_activity}" if last_activity else "Site live; no dated signal found"

    badges = []
    if fds:
        f0 = fds[0]
        badges.append({"kind": "form_d", "label": f"Form {f0['payload']['form']} {f0['event_date'][:7]}",
                       "recent": fd_days is not None and fd_days <= 365})
    if tl["rising"] or (hcp.get("growth_12") is not None and hcp["growth_12"] >= HEADCOUNT_GROWTH):
        badges.append({"kind": "rising", "label": "Activity rising"})
    if hcp.get("total"):
        g = hcp.get("growth_12")
        badges.append({"kind": "headcount", "label": f"{hcp['total']} people" + (f", {g:+}% in 12 mo" if g is not None else "")})
    if tl["fading"] and hcp and not headcount_falling:
        badges.append({"kind": "site_quiet", "label": "Website quieter, headcount isn't"})
    if warnings:
        badges.append({"kind": "news_warning", "label": "News: " + (warnings[0].get("tag") or "warning sign")})
    if hire and hire["payload"].get("open_roles"):
        badges.append({"kind": "hiring", "label": f"Hiring: {hire['payload']['open_roles']} open"})
    if fp and fp["payload"].get("redirected_off_domain") and not moved:
        badges.append({"kind": "redirect", "label": f"Now {fp['payload'].get('final_host')}"})
    if traffic and traffic["payload"].get("tranco_rank"):
        badges.append({"kind": "traffic", "label": f"Top {traffic['payload']['tranco_rank']:,} site"})
    if no_mail:
        badges.append({"kind": "no_mail", "label": "No email records"})
    if expires and not expired and (_days_since(expires, today) or 0) >= -60:
        badges.append({"kind": "expiring", "label": f"Domain expires {expires}"})

    evidence = {
        "rule": rule,
        "trend": tl,
        "badges": badges,
        "current": {"hiring_now": hiring_now, "shipping_now": shipping_now, "raised_now": raised_now,
                    "updating_now": updating_now},
        "mail": (mail or {}).get("payload"), "registration": (reg or {}).get("payload"),
        "subdomains": (certs or {}).get("payload", {}).get("names"),
        "headcount": hcp or None,
        "news": ((news or {}).get("payload") or {}).get("items", [])[:5],
        "thresholds": {"active_days": ACTIVE_DAYS, "fading_ratio": FADING_RATIO, "min_prior_events": MIN_PRIOR_EVENTS,
                       "p_value": P_VALUE, "min_expected": MIN_EXPECTED, "lag": lag,
                       "trend_months": [TREND_RECENT, TREND_PRIOR], "auto_link": cfg.auto_link, "reject": cfg.reject,
                       "weights": w},
        "signals": [{"date": d, "what": what, "observation_id": oid, "url": url} for d, what, oid, url in dated],
        "observation_ids": [o["id"] for o in (fp, eng, hire, web, certs, mail, reg, traffic, news, hc) if o] + [f["id"] for f in fds],
    }
    return {"form_d_score": round(s_form_d, 3), "engineering_score": round(s_eng, 3),
            "hiring_score": round(s_hire, 3), "footprint_score": round(s_fp, 3),
            "composite_score": round(min(composite, 1.0), 3), "bucket": bucket,
            "last_activity_date": last_activity, "evidence": evidence}


def portfolio_lag(conn, today: date) -> tuple[dict[str, dict[str, float]], date]:
    series = {}
    for src, kind in (("web", "web_history"), ("certs", "cert_history"), ("news", "news")):
        rows = conn.execute("""SELECT payload FROM signal_observations WHERE signal_type = ? AND id IN
                                 (SELECT MAX(id) FROM signal_observations WHERE signal_type = ? GROUP BY entity_id)""",
                            (kind, kind)).fetchall()
        series[src] = [json.loads(r[0]).get("months", {}) for r in rows]
    end = indexed_through(series["web"], today)
    return lag_factors(series, end), end


def score_all(conn, cfg: Config, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    counts: dict[str, int] = {}
    lag, end = portfolio_lag(conn, today)
    with conn:
        for e in [dict(r) for r in conn.execute("SELECT * FROM entities")]:
            s = score_entity(conn, cfg, e, today, lag, end)
            conn.execute("""INSERT INTO portfolio_daily_scores (entity_id, score_date, form_d_score, engineering_score,
                              hiring_score, footprint_score, composite_score, bucket, last_activity_date, evidence)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (entity_id, score_date) DO UPDATE SET
                              form_d_score = excluded.form_d_score, engineering_score = excluded.engineering_score,
                              hiring_score = excluded.hiring_score, footprint_score = excluded.footprint_score,
                              composite_score = excluded.composite_score, bucket = excluded.bucket,
                              last_activity_date = excluded.last_activity_date, evidence = excluded.evidence""",
                         (e["id"], today.isoformat(), s["form_d_score"], s["engineering_score"], s["hiring_score"],
                          s["footprint_score"], s["composite_score"], s["bucket"], s["last_activity_date"],
                          json.dumps(s["evidence"])))
            counts[s["bucket"]] = counts.get(s["bucket"], 0) + 1
    return counts
