// A local web page for the demo: the fund's pane requests a check-in and reads the report; the
// founder's pane approves, edits or withholds each drafted answer. Both agents run in this
// process over a local thread; the page polls /api/state.
import { existsSync, readFileSync, rmSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { join, resolve } from "node:path";
import { type Decision, finalize } from "./approval.ts";
import { loadDataroom } from "./dataroom.ts";
import type { CheckinRequest, CheckinResponse } from "./protocol.ts";
import { runStartup } from "./startup.ts";
import { LocalThread } from "./transport.ts";
import { runCheckin } from "./vc-agent.ts";

const ROOT = resolve(import.meta.dirname, "..");
for (const file of [join(ROOT, ".env"), join(ROOT, "..", ".env")]) if (existsSync(file)) process.loadEnvFile(file);

const COMPANY = "tallyline";
const FUND_DIR = join(ROOT, "fund", "northbeam");
const DATAROOM = join(ROOT, "dataroom", COMPANY);
const THREAD = join(ROOT, ".checkins", "threads", `${COMPANY}-web.jsonl`);
const PAGE = join(ROOT, "web", "index.html");
const PORT = Number(process.env.PORT ?? 4321);
const FOUNDER = process.env.FOUNDER_NAME ?? "Dana Okafor";

type Phase = "idle" | "vc_working" | "founder_drafting" | "awaiting_founder" | "done" | "error";
const state = {
  phase: "idle" as Phase,
  log: [] as { at: string; side: "vc" | "startup"; text: string }[],
  pending: null as null | { request: CheckinRequest; draft: CheckinResponse },
  sent: [] as { request: CheckinRequest; response: CheckinResponse }[],
  report: null as null | string,
  error: null as null | string,
};
let decide: ((d: Record<string, Decision>) => void) | null = null;

const log = (side: "vc" | "startup") => (text: string) => {
  state.log.push({ at: new Date().toISOString(), side, text: text.replace(/^\[(vc|startup)\] /, "") });
  console.log(text);
};

function start() {
  rmSync(THREAD, { force: true });
  Object.assign(state, { phase: "vc_working", log: [], pending: null, sent: [], report: null, error: null });
  const stop = new AbortController();
  const startup = runStartup(new LocalThread(THREAD, "startup"), async () => {
    state.phase = "founder_drafting";
    return loadDataroom(DATAROOM);
  }, { interactive: false, withhold: new Set(), approver: FOUNDER, outbox: join(ROOT, ".checkins", "outbox") }, {
    signal: stop.signal,
    log: log("startup"),
    approver: async (request, draft, docs) => {
      state.pending = { request, draft };
      state.phase = "awaiting_founder";
      const decisions = await new Promise<Record<string, Decision>>((r) => (decide = r));
      const response = finalize(draft, docs, decisions, FOUNDER);
      state.sent.push({ request, response });
      state.pending = null;
      state.phase = "vc_working";
      return response;
    },
  }).catch((e) => { if (!stop.signal.aborted) throw e; });

  runCheckin(new LocalThread(THREAD, "vc"), FUND_DIR, COMPANY, { log: log("vc") })
    .then(({ reportPath }) => {
      state.report = readFileSync(reportPath, "utf8");
      state.phase = "done";
    })
    .catch((e) => {
      state.error = e instanceof Error ? e.message : String(e);
      state.phase = "error";
    })
    .finally(async () => {
      stop.abort();
      await startup;
    });
}

async function body(req: IncomingMessage): Promise<unknown> {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

function send(res: ServerResponse, status: number, data: unknown, type = "application/json") {
  res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(type === "application/json" ? JSON.stringify(data) : String(data));
}

createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/") return send(res, 200, readFileSync(PAGE, "utf8"), "text/html; charset=utf-8");
    if (req.method === "GET" && url.pathname === "/api/state") return send(res, 200, state);
    if (req.method === "POST" && url.pathname === "/api/start") {
      if (!["idle", "done", "error"].includes(state.phase)) return send(res, 409, { error: "A check-in is already running." });
      start();
      return send(res, 200, { ok: true });
    }
    if (req.method === "POST" && url.pathname === "/api/approve") {
      if (state.phase !== "awaiting_founder" || !decide) return send(res, 409, { error: "Nothing is waiting for approval." });
      const { decisions } = (await body(req)) as { decisions: Record<string, Decision> };
      const d = decide;
      decide = null;
      d(decisions ?? {});
      return send(res, 200, { ok: true });
    }
    send(res, 404, { error: "not found" });
  } catch (e) {
    send(res, 500, { error: String(e) });
  }
}).listen(PORT, "127.0.0.1", () => console.log(`Check-ins demo on http://localhost:${PORT}`));
