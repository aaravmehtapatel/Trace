# Portfolio Check-ins

Founders stop replying to investor update requests because answering takes hours. Here, the fund's agent asks, the startup's agent answers from the company's own data room with a citation for every claim, and the founder approves the reply in two minutes. Nothing leaves the company without that approval.

The census in the parent folder watches companies from the outside, through public records. Check-ins ask them directly.

## What happens in one check-in

1. **The fund's agent asks.** It sends Northbeam's 8 standard questions, plus 2 to 4 questions it writes from what the company said last quarter ("Did the Meridian pilot launch in September?").
2. **The startup's agent drafts.** It answers from the data room only. Code then checks every citation, and a quote that isn't really in the file it names is thrown away. Code also sets each answer's tier:
   - **Verified:** backed by a system export, such as billing or bank data.
   - **Documented:** backed by a document the team wrote.
   - **Unanswered:** nothing in the data room supports an answer.

   `people/` (compensation) never reaches the model.
3. **The founder approves.** They approve, edit or withhold each answer. Withheld answers are sent as withheld, so the fund sees the question was declined, not missed.
4. **The fund's agent checks and follows up.** It recomputes runway, burn and churn from the raw exports the startup attached, without trusting the prose. It asks one round of follow-ups on what's missing or contradicted.
5. **It writes the report.** One page: a status, red flags with evidence, metrics against last quarter and plan, what wasn't answered, outside signals from akta, and questions for the next board meeting.

## The demo company

[`dataroom/tallyline/`](dataroom/tallyline/) is an invented restaurant-bookkeeping startup with four problems planted in its data room:

- **Runway:** the founder's model, written in May, says 22 months. The bank export shows burn rising to $214k a month, which is 11 months.
- **Churn:** it was reported in Q1 and Q2 at 2.4%, then dropped from Q3 notes ("not put a churn number in the board pre-read"). The billing export implies 9.7% a month.
- **Bank pilot:** the Meridian pilot announced in June is paused until SOC 2 Type II, a year out.
- **Bridge:** the founders are quietly discussing a bridge round and a possible acqui-hire.

The fund side is [`fund/northbeam/`](fund/northbeam/): standard questions, notes on Tallyline, and an akta headcount sample. The sample is synthetic, because a live lookup for an invented company would match a real business that has nothing to do with it.

## Run it

Node 20.12 or later.

```bash
cd checkins
npm install
cp .env.example .env        # add NICRRON_API_KEY (or ANTHROPIC_API_KEY)
npm test                    # no keys needed: a scripted model runs the whole flow
npm run demo                # both agents on this machine; you approve each answer
npm run demo -- --approve-all --withhold s7     # unattended, founder withholds "top risks"
```

The report is written to `fund/northbeam/reports/tallyline-2026-Q3.md`.

### Across two Roundtable workspaces

The fund and the startup each need a workspace. Sign up at roundtable.bot with event code `VCHACK`, then create a key for each under **Agents → Connect agent → AI assistant → Custom**.

```bash
# Startup laptop: put the data room in the startup's bank (optional; otherwise it reads the local folder)
npm run startup -- sync

# Fund laptop: creates a cross-workspace chat and prints a one-time link for the founder
npm run vc -- run --transport roundtable --save-to-bank

# Startup laptop, once the founder has accepted the link in the Roundtable dashboard
npm run startup -- run --transport roundtable --from-bank
```

Both sides talk only through the chat. The fund never sees the startup's files, only the approved answers and the exports they cite. Each packet is a readable headline plus JSON parts, kept under Roundtable's 8,000-character post limit.

**Not yet tested against the live Roundtable API.** The docs don't give the exact response fields for `create_thread` and `read_thread`. Run the first time with `CHECKINS_DEBUG=1` to print raw responses, and adjust `src/roundtable.ts` if a field name differs.

## Sponsor tools

| Tool | Where it's used |
|---|---|
| Roundtable | The agents' chat between workspaces (`src/transport.ts`, `src/roundtable.ts`). The startup's data room lives in its bank, and the fund's report is saved to its bank. |
| Nicrron | Every model call, using `anthropic/claude-opus-5-5` (`src/llm.ts`). Falls back to the Anthropic API when no Nicrron key is set. |
| PRISM | Every model call is traced, with one agent name per version (`src/tracing.ts`). For the challenge, run 20+ times with `CHECKINS_CACHE=off`, fix the problem PRISM finds, set `CHECKINS_AGENT_VERSION=v2`, and rerun on the same inputs. |
| akta | Live news and headcount for companies with a real domain (`src/akta.ts`). These are the same endpoints the census uses. |

## Files

| File | What it does |
|---|---|
| `src/protocol.ts` | The request and response packets, validated on both sides |
| `src/dataroom.ts` | Loads the data room, keeps `people/` out, checks quotes |
| `src/startup-agent.ts` | Drafts answers and grounds them: citations, tiers, attachments |
| `src/approval.ts` | The founder gate |
| `src/vc-agent.ts` | Plans questions, runs rounds, asks follow-ups |
| `src/checks.ts` | Runway, burn, implied churn, plan misses, silence flags |
| `src/report.ts` | The report. The model writes only the bottom line and the board questions, and can't rate a company better than its findings |
| `src/akta.ts` | Outside signals |
| `src/llm.ts`, `src/tracing.ts` | Model calls and PRISM |
