"""Grade the free public signals against licensed headcount (Akta).

For every company that has both, compare the status we'd give from free data alone with what
its headcount actually did over the last 6 months. The result says how far the free census can
be trusted, and which companies are worth paying for headcount on.
"""
import json
from datetime import date

from src import score
from src.config import Config

GROWING, SHRINKING = 5, -10   # % headcount change over 6 months


def headcount_class(p: dict) -> str | None:
    g = p.get("growth_6")
    if g is None:
        g = p.get("growth_12")
    if g is None:
        return None
    return "growing" if g >= GROWING else "shrinking" if g <= SHRINKING else "flat"


def calibrate(conn, cfg: Config, today: date | None = None) -> dict:
    today = today or date.today()
    lag, end = score.portfolio_lag(conn, today)
    rows = []
    for e in [dict(r) for r in conn.execute("SELECT * FROM entities WHERE main_domain IS NOT NULL")]:
        hc = score.latest(conn, e["id"], "headcount")
        if not hc:
            continue
        if (hc["payload"].get("total") or 0) > score.MAX_PLAUSIBLE_HEADCOUNT:
            continue  # a different, much larger company on the same website
        truth = headcount_class(hc["payload"])
        if not truth:
            continue
        free = score.score_entity(conn, cfg, e, today, lag, end, use_headcount=False)
        cur = free["evidence"]["current"]
        rows.append({"name": e["raw_name"], "free_status": free["bucket"], "truth": truth,
                     "growth_6": hc["payload"].get("growth_6"), "total": hc["payload"].get("total"),
                     "signals": {"site_updated_90d": bool(free["last_activity_date"]) and
                                 (score._days_since(free["last_activity_date"], today) or 999) <= 90,
                                 "hiring_now": cur["hiring_now"], "shipping_now": cur["shipping_now"],
                                 "raised_12mo": any(b["kind"] == "form_d" and b.get("recent") for b in free["evidence"]["badges"]),
                                 "has_email": not any(b["kind"] == "no_mail" for b in free["evidence"]["badges"])}})
    n = len(rows)
    if not n:
        return {"n": 0}

    def share(sel, pred):
        sel = [r for r in rows if sel(r)]
        return {"n": len(sel), "pct": round(100 * sum(1 for r in sel if pred(r)) / len(sel)) if sel else None}

    statuses = {}
    for st in ("Active", "Quiet", "Fading", "No Public Activity"):
        grp = [r for r in rows if r["free_status"] == st]
        statuses[st] = {"n": len(grp), **{k: sum(1 for r in grp if r["truth"] == k) for k in ("growing", "flat", "shrinking")}}
    # A free status "agrees" when Active is growing or flat, or a warning status is shrinking.
    agree = sum(1 for r in rows if (r["free_status"] == "Active" and r["truth"] != "shrinking")
                or (r["free_status"] in ("Fading", "No Public Activity") and r["truth"] == "shrinking"))
    judged = sum(1 for r in rows if r["free_status"] in ("Active", "Fading", "No Public Activity"))
    base_growing = round(100 * sum(1 for r in rows if r["truth"] == "growing") / n)
    signals = {}
    for sig in rows[0]["signals"]:
        on = share(lambda r, s=sig: r["signals"][s], lambda r: r["truth"] == "growing")
        off = share(lambda r, s=sig: not r["signals"][s], lambda r: r["truth"] == "growing")
        signals[sig] = {"with": on, "without": off}
    missed = sorted((r for r in rows if r["free_status"] in ("Quiet", "Fading", "No Public Activity") and r["truth"] == "growing"),
                    key=lambda r: -(r["growth_6"] or 0))[:5]
    hidden = sorted((r for r in rows if r["free_status"] == "Active" and r["truth"] == "shrinking"),
                    key=lambda r: (r["growth_6"] or 0))[:5]
    return {"n": n, "judged": judged, "agree_pct": round(100 * agree / judged) if judged else None,
            "base_growing_pct": base_growing, "by_free_status": statuses, "signals": signals,
            "quiet_but_growing": [{"name": r["name"], "growth_6": r["growth_6"], "total": r["total"]} for r in missed],
            "active_but_shrinking": [{"name": r["name"], "growth_6": r["growth_6"], "total": r["total"]} for r in hidden],
            "thresholds": {"growing": GROWING, "shrinking": SHRINKING}}
