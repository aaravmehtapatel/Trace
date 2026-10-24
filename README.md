# Portfolio Census

A monthly early-warning check on every company in a high-volume venture portfolio, built only from public records. Founders don't have to send anything.

For each company it answers: is this company still moving, and is that changing? Every answer links to the record behind it. The output a partner acts on is a short list of who to call this week, why, and what to ask.

Statuses come from the activity trend. Form D is a badge, not a status, because most private companies never file one (in the Everywhere run it covers 51 of 265).

| Status | How it's decided |
|---|---|
| Active | A dated public signal in the last 90 days, and no sign of decline |
| Fading | Dated activity in the last 3 months fell below 35% of the 9 months before, with no recent job post, code push or Form D to contradict it |
| Quiet | Site live, nothing dated in 90 days |
| No public activity | Site down, parked, expired or now another company's site; or no email records and nothing dated in a year |
| Unresolved | No verified website, so no status is given |

The trend only uses sources that keep their history, so last quarter can be compared fairly with the year before on the first run: Web Archive content changes, subdomains first seen in certificate-transparency logs, and Form D filings. Job postings, GitHub pushes and sitemap dates only show the present, so they count as proof of current activity, not as trend.

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
| History | `src/ingest/history.py` | Web Archive content changes, first-seen subdomains (crt.sh), email (MX) records, domain registration (RDAP), Tranco traffic rank |
| Footprint | `src/ingest/web_probe.py` | HTTP status, redirects, parked pages, TLS expiry, sitemap dates, the legal name in the copyright line, and links to GitHub and job boards |
| Engineering | `src/ingest/github_pub.py` | Public pushes and releases for the org the company's own site links to, with ETag caching |
| Hiring | `src/ingest/jobs.py` | Open roles from Greenhouse, Lever and Ashby public APIs |
| SEC | `src/ingest/sec_edgar.py` | Full-text search plus EDGAR's name lookup, then each filer's record and Form D XML for amounts |
| Matching | `src/match/resolve.py` | Jaro-Winkler, token-set and ratio scores, plus city or state corroboration. Auto-link at 0.92 or higher, review between 0.72 and 0.92, drop below 0.72 |
| Status | `src/score.py` | Trend rules decide the status; Form D, hiring, traffic and redirects are badges. The weighted score only ranks companies within a status |
| Calls | `src/report.py` | This week's call list: companies that went dark, then fading, then rising, each with the question to ask |

Every raw response is kept in `data/raw/<source>/<date>/` and never overwritten. Every observation row points to the dump it came from.

## Known limits

- Many startups never file a Form D, so it's a badge, never a status.
- The Web Archive crawls popular sites more often, so archive changes are a noisy proxy for activity. Capping each source per month keeps one source from dominating.
- "Fading" is a trend, not a prediction: it hasn't been backtested against outcomes yet.
- Common names collide. That's why exact-name matches without a city or state match go to review instead of auto-linking.
