# Trace

Portfolio census for high-volume venture funds. The business case is in [COMPANY.md](COMPANY.md); the plan is in [BUSINESS_PLAN.md](BUSINESS_PLAN.md).

A monthly early-warning check on every company in a high-volume venture portfolio, built only from public records. Founders don't have to send anything.

For each company it answers: is this company still moving, and is that changing? Every answer links to the record behind it. The output a partner acts on is a short list of who to call this week, why, and what to ask.

Statuses come from the activity trend. Form D is a badge, not a status, because most private companies never file one (in the Everywhere run it covers 51 of 265).

Free public checks run on every company. Licensed headcount and news from Akta (Wokelo), the LinkedIn-sourced data funds normally scrape, decide Fading where they exist, and the census grades its own free signals against that headcount. In a 10-company sample, free data alone got 5 wrong: it called Spade quiet while its headcount doubled. So free checks run for everyone, and licensed data is bought where free data is weakest.

| Status | How it's decided |
|---|---|
| Active | A dated public signal in the last 90 days, and no sign of decline |
| Fading | Headcount down 15% or more in 6 months (Akta); or, with no headcount, a drop in dated public activity unlikely by chance and nothing current to contradict it |
| Quiet | Site live, nothing dated in 90 days |
| No public activity | Site down, parked, expired or now another company's site; or no email records and nothing dated in a year |
| Unresolved | No verified website, so no status is given |

For the companies on that call list, [`checkins/`](checkins/) asks directly: the fund's agent sends questions, the startup's agent answers from its own data room with a citation for every claim, and the founder approves before anything is sent. It runs between workspaces on Roundtable. See [checkins/README.md](checkins/README.md).

**Run the whole product on one link.** The check-ins server also serves the front page and the latest census report, so Trace has one home:

```bash
cd checkins && npm install && npm run web
# http://localhost:4321          the front page (site/); "Try it out" opens the census
# http://localhost:4321/census   the census (Everywhere Ventures)
# http://localhost:4321/checkins check-ins (Tallyline, a synthetic demo company)
```

After a new `export`, run `node site/build-data.mjs` from the repo root so the front page shows the same census.

Every call in the census, and every company record, has a "Request a check-in" link that opens check-ins with the reason the census flagged it. A live check-in needs `CENSUS_ANTHROPIC_API_KEY` in the census `.env`, or a reply cache from an earlier run in `checkins/.checkins/cache`.

The trend only uses sources that keep their history, so last quarter can be compared fairly with the year before on the first run: Web Archive content changes, subdomains first seen in certificate-transparency logs, and Form D filings. Job postings, GitHub pushes and sitemap dates only show the present, so they count as proof of current activity, not as trend.

## Host it on Render

The repo has a [`render.yaml`](render.yaml) blueprint, so Render builds and starts Trace with no settings to type:

1. On [render.com](https://render.com), choose **New > Blueprint** and pick this repository.
2. Render asks for two values:
   - `TRACE_PASSWORD`: one shared password for the whole site. Anyone opening it is asked for it (any username works). Without it, anyone with the URL could see the licensed data and run check-ins on your model key.
   - `CENSUS_ANTHROPIC_API_KEY`: only needed for check-ins the committed reply cache doesn't cover. The Tallyline demo replays from the cache, so it can be left blank.
3. Deploy. The site is at `https://<your-service>.onrender.com`: the front page at `/`, the census at `/census`, check-ins at `/checkins`.

On the free plan the service sleeps when idle, so the first visit after a break takes about 50 seconds. Open it once before you present.

## Run it

```bash
cd census
pip install -r requirements.txt
cp .env.example .env              # set SEC_USER_AGENT (SEC fair-access rules); the Claude key is optional
python3 -m src.cli init           # pulls the fund's portfolio table into SQLite
python3 -m src.cli sync           # domains -> website/TLS/sitemap -> GitHub -> job boards -> SEC
python3 -m src.cli review-queue   # confirm or reject the matches it wasn't sure about
python3 -m src.cli score          # buckets, printed as a table
python3 -m src.cli export         # exports/census-<date>.{html,json,md,csv}; the CSV imports into a CRM
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
| Akta | `src/ingest/akta.py` | Licensed headcount (monthly, by function) and news, matched by exact website through Akta's company search |
| Grading | `src/calibrate.py` | Scores every company from free data alone and compares with what its headcount did |
| Calls | `src/report.py` | This week's call list: companies that went dark, then fading, then rising, each with the question to ask |

Every raw response is kept in `data/raw/<source>/<date>/` and never overwritten. Every observation row points to the dump it came from.

## Known limits

- Many startups never file a Form D, so it's a badge, never a status.
- The Web Archive crawls popular sites more often, so archive changes are a noisy proxy for activity. Capping each source per month keeps one source from dominating.
- "Fading" is a trend, not a prediction: it hasn't been backtested against outcomes yet.
- The Web Archive indexes recent months late. The trend ends at the last month most of the portfolio is indexed and scales each company against the portfolio baseline.
- crt.sh (certificate logs) was mostly down on the first run; the source fills in on later runs.
- Common names collide. That's why exact-name matches without a city or state match go to review instead of auto-linking.
