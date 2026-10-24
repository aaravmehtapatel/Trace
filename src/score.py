"""Buckets come from dated rules; the weighted composite only ranks companies within a bucket.

Rules, first match wins:
  Raised Again        a Form D or D/A from a linked filer within raise_window_months (an
                      amendment means the offering is still open)
  Unresolved          no verified website, so we don't classify the company
  No Public Activity  site down, parked or redirecting elsewhere; dated by the last signal we have
  Active Footprint    any dated signal (push, posting, sitemap update, Form D) within active_days
  Quiet Phase         site is up, nothing dated within active_days
"""
import json
import math
from datetime import date, timedelta

from src.config import Config

ACTIVE_DAYS = 90
RAISE_WINDOW_MONTHS = 24


def _days_since(d: str | None, today: date) -> int | None:
    if not d:
        return None
    try:
        return (today - date.fromisoformat(d[:10])).days
    except ValueError:
        return None


def _recency(days: int | None, horizon: int) -> float:
    return 0.0 if days is None or days < 0 else max(0.0, 1 - days / horizon)


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


def score_entity(conn, cfg: Config, e: dict, today: date) -> dict:
    fp, eng, hire = (latest(conn, e["id"], k) for k in ("footprint", "engineering", "hiring"))
    fds = filings(conn, e["id"])
    new_d = fds  # newest first; D or D/A
    w = cfg.weights

    # Signal scores, 0-1.
    fd_days = _days_since(new_d[0]["event_date"], today) if new_d else None
    raised = (new_d[0]["payload"].get("total_sold") or 0) if new_d else 0
    s_form_d = _recency(fd_days, RAISE_WINDOW_MONTHS * 30) * (0.7 + 0.3 * min(1, math.log10(raised + 1) / 7))
    s_eng = 0.0
    if eng:
        p = eng["payload"]
        s_eng = 0.6 * min(1, p["events_in_window"] / 30) + 0.4 * _recency(_days_since(p["last_push"], today), cfg.window_days * 3)
    s_hire = 0.0
    if hire:
        p = hire["payload"]
        s_hire = 0.6 * min(1, p["open_roles"] / 10) + 0.4 * _recency(_days_since(p["latest_posting"], today), ACTIVE_DAYS)
    s_fp = 0.0
    live = False
    if fp:
        p = fp["payload"]
        live = p.get("status") == 200 and not p.get("parked") and not p.get("redirected_off_domain")
        cert_days = -_days_since(p.get("cert_not_after"), today) if p.get("cert_not_after") else None
        s_fp = (0.5 if live else 0) + (0.2 if cert_days and cert_days > 14 else 0) \
            + 0.3 * _recency(_days_since(p.get("sitemap_lastmod"), today), ACTIVE_DAYS)
    composite = w["form_d"] * s_form_d + w["engineering"] * s_eng + w["hiring"] * s_hire + w["footprint"] * s_fp

    # Every dated signal, newest first: this is the evidence trail.
    dated = [(f["event_date"], f"Form {f['payload']['form']} filed", f["id"], f["source_url"]) for f in fds]
    if eng and eng["payload"].get("last_push"):
        dated.append((eng["payload"]["last_push"], f"Last GitHub push ({eng['payload']['login']})", eng["id"], eng["source_url"]))
    if hire and hire["payload"].get("latest_posting"):
        dated.append((hire["payload"]["latest_posting"], f"Job posted ({hire['payload']['open_roles']} open)", hire["id"], hire["source_url"]))
    if fp and fp["payload"].get("sitemap_lastmod"):
        dated.append((fp["payload"]["sitemap_lastmod"], "Website updated (sitemap)", fp["id"], fp["source_url"]))
    dated = sorted((d for d in dated if d[0]), reverse=True)
    last_activity = dated[0][0] if dated else None
    last_days = _days_since(last_activity, today)

    if new_d and fd_days is not None and fd_days <= RAISE_WINDOW_MONTHS * 30:
        bucket, rule = "Raised Again", f"Form {new_d[0]['payload']['form']} filed {new_d[0]['event_date']}"
    elif not e["main_domain"]:
        bucket, rule = "Unresolved", "No verified website yet"
    elif not live:
        why = "parked" if fp and fp["payload"].get("parked") else \
            f"now redirects to {fp['payload'].get('final_host')}, often an acquisition or rebrand" \
            if fp and fp["payload"].get("redirected_off_domain") else \
            f"site returned {fp['payload'].get('status') or fp['payload'].get('error')}" if fp else "site not probed"
        bucket, rule = "No Public Activity", f"Website {why}"
    elif last_days is not None and last_days <= ACTIVE_DAYS:
        bucket, rule = "Active Footprint", f"{dated[0][1]} {last_activity}"
    else:
        bucket = "Quiet Phase"
        rule = f"Site live; last dated signal {last_activity}" if last_activity else "Site live; no dated signal found"

    evidence = {
        "rule": rule,
        "thresholds": {"active_days": ACTIVE_DAYS, "raise_window_months": RAISE_WINDOW_MONTHS,
                       "auto_link": cfg.auto_link, "reject": cfg.reject, "weights": w},
        "signals": [{"date": d, "what": what, "observation_id": oid, "url": url} for d, what, oid, url in dated],
        "observation_ids": [o["id"] for o in (fp, eng, hire) if o] + [f["id"] for f in fds],
    }
    return {"form_d_score": round(s_form_d, 3), "engineering_score": round(s_eng, 3),
            "hiring_score": round(s_hire, 3), "footprint_score": round(s_fp, 3),
            "composite_score": round(min(composite, 1.0), 3), "bucket": bucket,
            "last_activity_date": last_activity, "evidence": evidence}


def score_all(conn, cfg: Config, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    counts: dict[str, int] = {}
    with conn:
        for e in [dict(r) for r in conn.execute("SELECT * FROM entities")]:
            s = score_entity(conn, cfg, e, today)
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
