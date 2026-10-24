# Portfolio Census

A monthly status check on every company in a high-volume venture portfolio, built only from public records. Founders don't have to send anything.

For each company it answers one question with evidence: did it raise again, is it active, has it gone quiet, or has it gone dark? Every answer links to the filing, page or commit behind it.

Run on Everywhere Ventures' public portfolio (265 companies, 24 Oct 2026):

| Status | Companies | How it's decided |
|---|---|---|
| Raised again | 21 | A Form D or D/A from a matched SEC filer in the last 24 months |
| Active | 81 | A dated public signal in the last 90 days: code push, job posting, site update |
| Quiet | 90 | Site live, nothing dated in 90 days |
| No public activity | 24 | Site down, parked, or redirecting elsewhere (Aerodome now redirects to its acquirer, Flock Safety) |
| Unresolved | 49 | No website verified, so no status is given |

A hand check of 20 random classified companies found every site and filer consistent with the fund's own description.

## Run it

```bash
cd census
pip install -r requirements.txt
cp .env.example .env              # set SEC_USER_AGENT (SEC fair-access rules); the Claude key is optional
python3 -m src.cli init           # pulls the fund's portfolio table into SQLite
python3 -m src.cli sync           # domains -> website/TLS/sitemap -> GitHub -> job boards -> SEC
python3 -m src.cli review-queue   # confirm or reject the matches it wasn't sure about
python3 -m src.cli score          # buckets, printed as a table
python3 -m src.cli export         # exports/census-<date>.{html,json,md}
python3 -m pytest tests
```

GitHub uses the `gh` CLI's token when present. Everything else needs no login.

## How it works

| Step | File | What it does |
|---|---|---|
| Portfolio | `src/ingest/portfolio.py` | Reads the fund's own public portfolio table (name, city, sector, description) |
| Websites | `src/ingest/domains.py` | Candidate domains from Claude, then name patterns. A site counts only when the live page names the company and describes what the fund says it does |
| Footprint | `src/ingest/web_probe.py` | HTTP status, redirects, parked pages, TLS expiry, sitemap dates, the legal name in the copyright line, and links to GitHub and job boards |
| Engineering | `src/ingest/github_pub.py` | Public pushes and releases for the org the company's own site links to, with ETag caching |
| Hiring | `src/ingest/jobs.py` | Open roles from Greenhouse, Lever and Ashby public APIs |
| SEC | `src/ingest/sec_edgar.py` | Full-text search plus EDGAR's name lookup, then each filer's record and Form D XML for amounts |
| Matching | `src/match/resolve.py` | Jaro-Winkler, token-set and ratio scores, plus city or state corroboration. Auto-link at 0.92 or higher, review between 0.72 and 0.92, drop below 0.72 |
| Status | `src/score.py` | Dated rules decide the status. The weighted score only ranks companies within a status |

Every raw response is kept in `data/raw/<source>/<date>/` and never overwritten. Every observation row points to the dump it came from.

## Known limits

- Many startups never file a Form D, so "raised again" is a floor, not a count.
- Form D appears after a round starts, so this is a census, not an early warning.
- Common names collide. That's why exact-name matches without a city or state match go to review instead of auto-linking.
