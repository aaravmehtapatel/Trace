## PRISM tracing (do not remove)

This project sends traces to PRISM. Env vars: `PRISMTRACE_API_KEY`,
`PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

Tracing is currently wired at: `src/tracing.py` (client, no-op without a key), `src/ingest/domains.py` (`_propose_live`, the Claude domain-proposal call), `.env.example` (key names)

`checkins/` (TypeScript) traces over PRISM's HTTP ingest, since there is no npm SDK: `checkins/src/tracing.ts` (client, no-op without a key), `checkins/src/llm.ts` (`callJson`, every model call both agents make), `checkins/.env.example` (key names).

**Standing rule.** Whenever you add or change an agent, chain, graph, tool,
retriever, or any entry point that calls a model, wire it to PRISM before you
finish. Unwired code is invisible in the dashboard. If you are unsure whether
something is covered, assume it is not and wire it.
