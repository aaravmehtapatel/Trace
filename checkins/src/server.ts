// A local web page for the demo: the fund's pane requests a check-in and reads the report; the
// founder's pane approves, edits or withholds each drafted answer. Both agents run in this
// process over a local thread; the page polls /api/state.
import { existsSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { extname, join, resolve, sep } from "node:path";
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
const EXPORTS = join(ROOT, "..", "exports");
const SITE = join(ROOT, "..", "site");

// Trace runs on one origin: "/" is the front page in site/, "/census" is the latest census
// export, "/checkins" is this page.
const SITE_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
  ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml",
};
// The front page and the files it loads (census-data.js, assets/); never the build script.
function siteFile(pathname: string): { body: Buffer; type: string } | null {
  const rel = pathname === "/" ? "index.html" : decodeURIComponent(pathname).replace(/^\/+/, "");
  const file = resolve(SITE, rel);
  const type = SITE_TYPES[extname(file)];
  if (!type || !file.startsWith(SITE + sep) || !existsSync(file) || !statSync(file).isFile()) return null;
  return { body: readFileSync(file), type };
}
function censusPage(): string | null {
  if (!existsSync(EXPORTS)) return null;
  const latest = readdirSync(EXPORTS).filter((f) => /^census-\d{4}-\d{2}-\d{2}\.html$/.test(f)).sort().pop();
  return latest ? readFileSync(join(EXPORTS, latest), "utf8") : null;
}
const PORT = Number(process.env.PORT ?? 4321);
// Locally Trace listens on loopback only; on a host such as Render (which sets RENDER and PORT)
// it has to accept outside connections.
const HOST = process.env.HOST ?? (process.env.RENDER ? "0.0.0.0" : "127.0.0.1");
// Hosted publicly, one shared password keeps strangers off the licensed data and from spending
// the model key on check-ins. Unset locally, so nothing changes on a laptop.
const PASSWORD = process.env.TRACE_PASSWORD;
function authorized(req: IncomingMessage): boolean {
  if (!PASSWORD) return true;
  const [scheme, value] = (req.headers.authorization ?? "").split(" ");
  if (scheme !== "Basic" || !value) return false;
  const pass = Buffer.from(value, "base64").toString("utf8").split(":").slice(1).join(":");
  return pass.length === PASSWORD.length && timingSafeEqual(Buffer.from(pass), Buffer.from(PASSWORD));
}
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
    if (url.pathname === "/healthz") return send(res, 200, { ok: true });
    if (!authorized(req)) {
      res.writeHead(401, { "WWW-Authenticate": 'Basic realm="Trace", charset="UTF-8"', "Content-Type": "text/plain" });
      return res.end("Trace needs the shared password.");
    }
    if (req.method === "GET" && (url.pathname === "/census" || url.pathname === "/census/")) {
      const census = censusPage();
      if (!census) { res.writeHead(302, { Location: "/checkins" }); return res.end(); }
      return send(res, 200, census, "text/html; charset=utf-8");
    }
    if (req.method === "GET" && (url.pathname === "/checkins" || url.pathname === "/checkins/"))
      return send(res, 200, readFileSync(PAGE, "utf8"), "text/html; charset=utf-8");
    if (req.method === "GET" && url.pathname === "/api/state") return send(res, 200, state);
    if (req.method === "POST" && url.pathname === "/api/start") {
      if (!["idle", "done", "error"].includes(state.phase)) return send(res, 409, { error: "A check-in is already running." });
      start();
      return send(res, 200, { ok: true });
    }
    if (req.method === "GET") {
      const page = siteFile(url.pathname);
      if (page) {
        res.writeHead(200, { "Content-Type": page.type, "Cache-Control": "no-store" });
        return res.end(page.body);
      }
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
}).listen(PORT, HOST, () => console.log(`Trace on http://localhost:${PORT} (front page), http://localhost:${PORT}/census and http://localhost:${PORT}/checkins${PASSWORD ? " (password required)" : ""}`));
