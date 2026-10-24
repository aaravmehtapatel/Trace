"""`sync`: portfolio -> domains -> web/GitHub/jobs -> Form D, all written to SQLite."""
import asyncio
import json
import re
import sqlite3
from typing import Callable

from src.config import Config
from src.ingest import akta, domains, github_pub, history, jobs, portfolio, sec_edgar, web_probe
from src.match.normalize import normalize
from src.match.resolve import score_candidate, state_for

Progress = Callable[[str, int, int], None]


def _observe(conn, entity_id: int, kind: str, payload: dict, score: float | None = None,
             event_date: str | None = None, source_url: str | None = None, raw: list[str] | str | None = None):
    raw_path = raw if isinstance(raw, str) or raw is None else ";".join(raw)
    conn.execute("""INSERT INTO signal_observations (entity_id, signal_type, event_date, source_url, payload, score, raw_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (entity_id, kind, event_date, source_url, json.dumps(payload), score, raw_path))


def _candidate(conn, entity_id: int, source: str, name: str, key: str, m: dict, state: str):
    """Insert or refresh a candidate. A human decision (CONFIRMED/REJECTED after review) is never overwritten."""
    conn.execute("""INSERT INTO match_candidates (entity_id, source, candidate_name, candidate_key, jaro_winkler,
                                                  token_set, domain_match, score, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (entity_id, source, candidate_key) DO UPDATE SET
                      jaro_winkler = excluded.jaro_winkler, token_set = excluded.token_set,
                      domain_match = excluded.domain_match, score = excluded.score,
                      state = CASE WHEN match_candidates.reviewed_at IS NOT NULL THEN match_candidates.state
                                   ELSE excluded.state END""",
                 (entity_id, source, name, key, m.get("jaro_winkler"), m.get("token_set"),
                  m.get("domain_match", 0), m["score"], state))


def load_portfolio(conn: sqlite3.Connection) -> int:
    rows = portfolio.fetch_all()
    with conn:
        for r in rows:
            conn.execute("""INSERT INTO entities (raw_name, normalized_name, city, sector, description)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT (raw_name) DO UPDATE SET city = excluded.city,
                              sector = excluded.sector, description = excluded.description""",
                         (r["name"], normalize(r["name"]), r["city"], r["sector"], r["description"]))
    return len(rows)


async def resolve_domains(conn, cfg: Config, progress: Progress):
    todo = [dict(r) for r in conn.execute(
        """SELECT * FROM entities e WHERE main_domain IS NULL AND NOT EXISTS (
             SELECT 1 FROM match_candidates m WHERE m.entity_id = e.id AND m.source = 'domain'
             AND m.reviewed_at IS NOT NULL)""")]
    if not todo:
        return
    progress("Proposing domains", 0, len(todo))
    proposals = await asyncio.to_thread(domains.propose, todo)
    if akta.available():
        from_akta = await domains.from_akta(todo)
        proposals = {c["id"]: list(dict.fromkeys(from_akta.get(c["id"], []) + proposals.get(c["id"], []))) for c in todo}
    f = web_probe.fetcher(cfg.probe_timeout, cfg.probe_concurrency)
    done = 0

    async def one(c):
        nonlocal done
        best = None
        tried = []
        for d in proposals.get(c["id"], []) + domains.patterns(c["raw_name"]):
            if d in tried:
                continue
            if best and d not in proposals.get(c["id"], []):
                break  # a proposal verified; skip the pattern guesses
            tried.append(d)
            p, raw = await web_probe.probe(f, d, cfg.probe_timeout)
            v = domains.verify(c["raw_name"], p, cfg.auto_link, cfg.reject, description=c["description"],
                               guessed=d not in proposals.get(c["id"], []))
            _candidate(conn, c["id"], "domain", p.get("title") or d, d, {"score": v["score"]}, v["state"])
            _observe(conn, c["id"], "domain", {"domain": d, **v, "title": p.get("title")}, v["score"],
                     source_url=f"https://{d}/", raw=raw)
            if v["state"] == "AUTO_LINKED" and (best is None or v["score"] > best[1]):
                best = (d, v["score"])
        if best:
            conn.execute("UPDATE entities SET main_domain = ? WHERE id = ?", (best[0], c["id"]))
        done += 1
        progress("Verifying domains", done, len(todo))

    try:
        await asyncio.gather(*(one(c) for c in todo))
    finally:
        await f.close()
    conn.commit()


async def probe_footprints(conn, cfg: Config, progress: Progress):
    targets = [dict(r) for r in conn.execute("SELECT * FROM entities WHERE main_domain IS NOT NULL")]
    web, gh, jb = web_probe.fetcher(cfg.probe_timeout, cfg.probe_concurrency), github_pub.fetcher(), jobs.fetcher()
    done = 0

    async def one(c):
        nonlocal done
        p, raw = await web_probe.probe(web, c["main_domain"], cfg.probe_timeout)
        p.pop("body_sample", None)
        if p.get("legal_name") and not p.get("redirected_off_domain"):  # a redirect's footer names the new owner
            conn.execute("UPDATE entities SET legal_name = ? WHERE id = ?", (p["legal_name"], c["id"]))
        _observe(conn, c["id"], "footprint", p, event_date=p.get("sitemap_lastmod"),
                 source_url=f"https://{c['main_domain']}/", raw=raw)

        logins = p.get("github") or ([c["github_org"]] if c["github_org"] else [])
        for login in logins[:2]:
            a, raw = await github_pub.activity(gh, login, cfg.window_days)
            if a["found"]:
                _observe(conn, c["id"], "engineering", a, event_date=a["last_push"], source_url=a["url"], raw=raw)
                if not c["github_org"]:
                    conn.execute("UPDATE entities SET github_org = ? WHERE id = ?", (login, c["id"]))
                break

        if p.get("ats"):
            o, raw = await jobs.openings(jb, p["ats"])
            if o:
                _observe(conn, c["id"], "hiring", o, event_date=o["latest_posting"], source_url=o["url"], raw=raw)
        done += 1
        progress("Probing sites, GitHub, job boards", done, len(targets))

    try:
        await asyncio.gather(*(one(c) for c in targets))
    finally:
        for f in (web, gh, jb):
            await f.close()
    conn.commit()


async def collect_history(conn, cfg: Config, progress: Progress):
    """Dated history per verified site: archive changes, new subdomains, email, registration, traffic rank."""
    targets = [dict(r) for r in conn.execute("SELECT * FROM entities WHERE main_domain IS NOT NULL")]
    ranks = await asyncio.to_thread(history.tranco_ranks)
    f = history.fetcher()
    done = 0

    async def one(c):
        nonlocal done
        d = c["main_domain"]
        calls = (("web_history", history.web_changes), ("cert_history", history.new_subdomains),
                 ("mail", history.mail), ("domain_reg", history.registration))
        results = await asyncio.gather(*(call(f, d) for _, call in calls))  # four different hosts at once
        for (kind, _), (payload, raw) in zip(calls, results):
            if payload.get("ok"):
                _observe(conn, c["id"], kind, payload, raw=raw)
        rank = ranks.get(d) or ranks.get(d.removeprefix("www."))
        _observe(conn, c["id"], "traffic", {"tranco_rank": rank, "list_date": history.date.today().isoformat()},
                 source_url="https://tranco-list.eu/")
        done += 1
        progress("Collecting dated history", done, len(targets))

    try:
        await asyncio.gather(*(one(c) for c in targets))
    finally:
        await f.close()
    conn.commit()


async def collect_akta(conn, cfg: Config, progress: Progress):
    """Licensed news and headcount from Akta, skipped when no key is set or the plan lacks an endpoint."""
    if not akta.available():
        progress("Akta skipped (no AKTA_API_KEY)", 1, 1)
        return
    targets = [dict(r) for r in conn.execute("SELECT * FROM entities WHERE main_domain IS NOT NULL")]
    # Headcount already pulled (e.g. through the connector) isn't bought twice.
    have_hc = {r[0] for r in conn.execute("SELECT DISTINCT entity_id FROM signal_observations WHERE signal_type = 'headcount'")}
    f, f_hc = akta.fetcher(), akta.fetcher(per_minute=9)
    done, headcount_allowed = 0, True

    async def one(c):
        nonlocal done, headcount_allowed
        n, raw = await akta.news(f, c["main_domain"])
        if n["ok"]:
            _observe(conn, c["id"], "news", n, event_date=(n["items"][0]["date"] if n["items"] else None), raw=raw)
        if headcount_allowed and c["id"] not in have_hc:
            h, raw = await akta.headcount(f, c["main_domain"], f_hc)
            if h["status"] in (401, 403):
                headcount_allowed = False  # not on this plan; stop spending requests on it
            elif h["ok"]:
                _observe(conn, c["id"], "headcount", h, raw=raw)
        conn.commit()  # long, paid run: keep what's been bought even if it's interrupted
        done += 1
        progress("Akta news and headcount", done, len(targets))

    try:
        await asyncio.gather(*(one(c) for c in targets))
    finally:
        await f.close()
        await f_hc.close()
    conn.commit()


async def form_d_detail(conn, f, entity_id: int, cand: dict, raw_search: str | None):
    """Store one observation per filing of a linked filer, newest three with their XML amounts."""
    info, raw_hist = await sec_edgar.filer(f, cand["cik"])
    if info["filings"]:
        cand = {**cand, "filings": info["filings"]}
        raw_search = ";".join(r for r in (raw_search, raw_hist) if r)
    for filing in cand["filings"][:3]:
        d, raw = await sec_edgar.filing_detail(f, cand["cik"], filing["adsh"])
        _observe(conn, entity_id, "form_d", {"cik": cand["cik"], "filer": cand["name"], "locations": cand["locations"],
                                             **filing, **d}, event_date=filing["file_date"], source_url=d["url"],
                 raw=[r for r in (raw_search, raw) if r])


async def search_form_d(conn, cfg: Config, progress: Progress):
    entities = [dict(r) for r in conn.execute("SELECT * FROM entities")]
    f = sec_edgar.fetcher(cfg.sec_user_agent)
    done = 0

    async def one(c):
        nonlocal done
        cands, raw = await sec_edgar.search(f, c["raw_name"])
        raws = [raw]
        legal = re.sub(r",?\s*(inc|llc|ltd|corp|pbc|co)\.?$", "", c["legal_name"] or "", flags=re.I)
        if legal and normalize(legal) != normalize(c["raw_name"]):
            more, r2 = await sec_edgar.search(f, legal)
            known = {x["cik"] for x in cands}
            cands += [x for x in more if x["cik"] not in known]
            raws.append(r2)
        # Name lookup catches filers that full-text search misses. Only plausible names
        # cost a second request for the filer's record.
        for q in {c["raw_name"], legal} - {""}:
            found, r3 = await sec_edgar.lookup(f, q)
            raws.append(r3)
            known = {x["cik"] for x in cands}
            for hit in found:
                if hit["cik"] in known:
                    continue
                pre = score_candidate(c["raw_name"], None, c["main_domain"], hit["name"], [], legal_name=c["legal_name"])
                if pre["score"] < cfg.reject - 0.25 * 0.75:  # can't reach review even with a city match
                    continue
                info, r4 = await sec_edgar.filer(f, hit["cik"])
                raws.append(r4)
                if info["filings"]:
                    cands.append({**info, "name": info["name"] or hit["name"]})
                    known.add(hit["cik"])
        raw = ";".join(r for r in raws if r)
        # Filers this run no longer finds (e.g. found via a legal name since cleared) drop out,
        # unless a person decided on them.
        seen = [x["cik"] for x in cands]
        conn.execute(f"""UPDATE match_candidates SET state = 'REJECTED' WHERE entity_id = ? AND source = 'sec_edgar'
                         AND reviewed_at IS NULL AND candidate_key NOT IN ({",".join("?" * len(seen)) or "''"})""",
                     (c["id"], *seen))
        for cand in cands:
            m = score_candidate(c["raw_name"], c["city"], c["main_domain"], cand["name"], cand["locations"],
                                legal_name=c["legal_name"])
            state = state_for(m["score"], cfg.auto_link, cfg.reject)
            _candidate(conn, c["id"], "sec_edgar", cand["name"], cand["cik"], m, state)
            final = conn.execute("""SELECT state FROM match_candidates WHERE entity_id = ? AND source = 'sec_edgar'
                                    AND candidate_key = ?""", (c["id"], cand["cik"])).fetchone()["state"]
            if final in ("AUTO_LINKED", "CONFIRMED"):
                if final == "AUTO_LINKED" and not c["cik"]:
                    try:
                        conn.execute("UPDATE entities SET cik = ? WHERE id = ? AND cik IS NULL", (cand["cik"], c["id"]))
                    except sqlite3.IntegrityError:  # same filer already linked to another company
                        pass
                await form_d_detail(conn, f, c["id"], cand, raw)
        done += 1
        progress("Searching SEC Form D", done, len(entities))

    try:
        await asyncio.gather(*(one(c) for c in entities))
    finally:
        await f.close()
    conn.commit()


STAGES = ("domains", "footprint", "history", "akta", "sec")


async def sync(conn, cfg: Config, progress: Progress, skip_domains: bool = False, only: str | None = None):
    run = {only} if only else set(STAGES) - ({"domains"} if skip_domains else set())
    if "domains" in run:
        await resolve_domains(conn, cfg, progress)
    if "footprint" in run:
        await probe_footprints(conn, cfg, progress)
    if "history" in run:
        await collect_history(conn, cfg, progress)
    if "akta" in run:
        await collect_akta(conn, cfg, progress)
    if "sec" in run:
        await search_form_d(conn, cfg, progress)
