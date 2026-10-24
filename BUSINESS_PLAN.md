# Trace: business plan

*Draft, 24 Oct 2026. Numbers from the first run are measured. Anything marked **(assumption)** is a hypothesis to test, not a finding.*

## 1. Summary

Trace tells a venture fund with more companies than people which portfolio companies need a partner this week, and why, without asking founders for anything. It combines free public checks with licensed headcount and news, gives every company a status with its evidence, and sends partners a monthly digest and call list.

The first run, on Everywhere Ventures' 265-company portfolio, found 7 raises worth about $145M in the last 30 days, 3 exits nobody had reported, and the portfolio's headcount (9,958 people, up 17% in a year). Licensed data for the whole run cost $18. An investor at the Deal Flow hackathon said they'd use it.

**Ask:** 3 design-partner funds running Trace monthly for 90 days.

## 2. Problem

- High-volume pre-seed funds, accelerators and scout programs hold hundreds of companies with a few partners.
- At pre-seed, most founders don't send regular updates. Every portfolio tool (Carta, Standard Metrics, Visible) depends on founders reporting.
- So the fund's view of its own portfolio is a spreadsheet that's stale the day it's made. That spreadsheet drives quarterly marks, write-offs, LP letters and the pitch for the next fund.
- Partners learn a company is struggling when it asks for a bridge, and learn about exits when a lawyer emails.

## 3. Customers

| Segment | Who decides | Why they buy | Priority |
|---|---|---|---|
| High-volume pre-seed and seed funds (150+ companies) | Managing partner, platform lead | Marks, LP letters, knowing who to help | **Start here** |
| Accelerators and corporate venture programs | Program director | Reporting alumni outcomes to their own backers | Next |
| Scout programs and angel syndicates | Program lead | Tracking hundreds of small checks | Next |
| LPs, fund-of-funds, secondary buyers | Investment team | They can't get founder updates at all; they check GP marks and price stakes | Expansion, higher price |

## 4. Product

**Today (built):**
- Monthly census of every company: website and its archived history, email and domain records, GitHub, job boards, SEC Form D, licensed headcount and news (Akta).
- Statuses with evidence: active, fading, quiet, no public activity, unresolved.
- "This month": raises, exits and renames, headcount movers.
- Weekly call list with the question to ask; CSV export for the fund's CRM.
- Self-grading: how often free data agrees with licensed headcount (80% on the first run), and where it's weakest.

**Next 6 months (roadmap):**
1. **Evidence-triggered check-ins:** an agent emails the founder only when Trace sees a reason (headcount drop, quiet site, a raise), asks the specific question, reads the reply and updates the status.
2. **Monthly email and CRM sync** (Affinity, Attio).
3. **Ask Trace:** plain-language questions answered by a local open-weight model, so fund data never leaves the firm.
4. **Runway watch:** time since last known raise plus the headcount trend, as an estimate of who's about to need money.
5. **LP view:** the same census across every fund an LP holds, showing overlap and concentration.

## 5. Business model

**Measured unit economics (first run):**
- Licensed data: 460 Akta credits, about **$18 for 265 companies, 7 cents a company** per monthly run.
- Public checks: free. Compute: a laptop, about 30 minutes unattended.

**Pricing (assumption, to test with design partners):**

| Plan | Price | Example |
|---|---|---|
| Fund | $5 per portfolio company per month | Everywhere (265 companies): about $16K a year |
| Accelerator | $3 per company per month, 1,000+ companies | 2,000 companies: about $72K a year |
| LP | $500 per GP fund monitored per year | An LP in 30 funds: $15K a year |

At these prices, data is under 2% of revenue. The real costs are sales and the trust it takes to get a partner to act on a call.

## 6. Go-to-market

**The product sells itself before the sale.** Trace needs no customer data, only a fund's public portfolio page. So the outbound motion is the product:

1. Pick funds with a public portfolio of 150+ companies.
2. Run Trace on each one.
3. Send the partner their own census: "7 of your companies raised last month, and 3 exited. Here's who to call."
4. Convert the ones who reply into design partners, then paid plans.

**First 90 days:**
- Run Trace monthly for Everywhere and 2 more funds.
- Track how many calls led to an action: a call made, a mark changed, an LP-letter line written.
- Backtest "fading": did fading companies raise a bridge, shut down or get acquired within 6 months?

## 7. Market size (assumptions, to verify)

Bottom-up, not top-down:
- **Target accounts (assumption):** a few hundred funds, accelerators and scout programs in the US and Europe hold 150+ companies each.
- **Formula:** accounts × average companies × price × 12.
- **Example scenario (assumption):** 300 accounts × 400 companies × $4 a month ≈ **$5.8M a year** from funds and accelerators alone.
- **Expansion:** the LP and secondaries segment is larger per account, since one LP holds many funds.

The first job is to replace these assumptions with counts from public portfolio pages, which Trace can scrape for us.

## 8. Competition

| Alternative | What it does | Why Trace wins or coexists |
|---|---|---|
| Carta, Standard Metrics, Visible | Collect founder-reported data | Coexists: Trace fills in the companies that don't report |
| Harmonic, Specter, PitchBook | Databases for finding deals | Trace is built around your own portfolio and its decisions |
| Akta / data vendors | Sell signals | Supplier: Trace buys their data and turns it into calls |
| An analyst with a spreadsheet | Manual checks, a few companies a week | Trace checks every company monthly, for cents |

## 9. What's defensible

1. **Company matching tested on real errors:** namesakes (Otis Elevator vs Otis the vet startup), renames (Mr Yum to me&u), redirects to acquirers, investment vehicles in SEC filings.
2. **History that compounds:** every monthly run adds a dated record per company that a new entrant can't backfill.
3. **The grading data:** each run measures which free signals predict what licensed data later shows, so Trace knows where to trust and where to pay.

## 10. Financial plan (scenario)

| Milestone | When | Revenue (assumption) |
|---|---|---|
| 3 design partners, free | Months 0–3 | $0 |
| 10 paying funds, about 250 companies each | Month 9 | about $150K ARR |
| 30 funds plus 3 accelerators (1,000 companies each) | Month 18 | about $560K ARR |

Costs in the first 18 months are mostly the founder's time and data. Data runs well under 5% of revenue, based on the measured 7 cents a company.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Funds have small software budgets | Start with the highest-volume portfolios; expand to LPs and secondary buyers, who pay more per account |
| Data platforms add a portfolio view | Move faster on the workflow (calls, marks, LP letters) and the grading data |
| 80% accuracy isn't enough for marks | Sell it as a call list first, publish accuracy every run, and backtest before claiming predictions |
| Licensed-data terms limit what can be shown | Keep licensed data inside the customer's report; never republish it publicly |
| Founders object to being monitored | Public and licensed data only, no scraping; check-ins come from their own investors |

## 12. Metrics we'll track

- Calls acted on per month, and the time from a signal to a partner acting on it.
- Agreement with licensed headcount (80% today), and the backtested accuracy of "fading."
- Share of companies placed (87% today), and unresolved companies cleared.
- Design partners converting to paid; net revenue retention.
