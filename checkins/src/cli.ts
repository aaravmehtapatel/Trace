// Entry points. `demo` runs both agents on this machine; `vc` and `startup` run one side
// each, talking over a Roundtable chat (two workspaces) or a local file.
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ApprovalMode } from "./approval.ts";
import { loadDataroom } from "./dataroom.ts";
import { RoundtableHttp } from "./roundtable.ts";
import { bankDataroom, runStartup, syncDataroom } from "./startup.ts";
import { LocalThread, RoundtableThread, type Thread } from "./transport.ts";
import { runCheckin } from "./vc-agent.ts";

const ROOT = resolve(import.meta.dirname, "..");
if (existsSync(join(ROOT, ".env"))) process.loadEnvFile(join(ROOT, ".env"));

const args = process.argv.slice(2);
const flags = new Map<string, string>();
const positional: string[] = [];
for (let i = 0; i < args.length; i++) {
  if (!args[i].startsWith("--")) { positional.push(args[i]); continue; }
  const next = args[i + 1];
  flags.set(args[i].slice(2), next && !next.startsWith("--") ? (i++, next) : "true");
}
const [side, cmd = "run"] = positional;
const flag = (k: string, d?: string) => flags.get(k) ?? d;

const COMPANY = flag("company", "tallyline")!;
const FUND_DIR = resolve(ROOT, flag("fund-dir", "fund/northbeam")!);
const DATAROOM = resolve(ROOT, flag("dataroom", `dataroom/${COMPANY}`)!);
const LOCAL_THREAD = join(ROOT, ".checkins", "threads", `${COMPANY}.jsonl`);

function approvalMode(): ApprovalMode {
  return { interactive: flag("approve-all") !== "true", withhold: new Set((flag("withhold") ?? "").split(",").filter(Boolean)),
    approver: process.env.FOUNDER_NAME ?? "Dana Okafor", outbox: join(ROOT, ".checkins", "outbox") };
}

function rt(which: "VC" | "STARTUP") {
  const key = process.env[`ROUNDTABLE_${which}_KEY`];
  if (!key) throw new Error(`Set ROUNDTABLE_${which}_KEY in checkins/.env (Agents -> Connect agent -> AI assistant -> Custom).`);
  return new RoundtableHttp(key);
}

async function pickThread(which: "VC" | "STARTUP", author: string): Promise<Thread> {
  if (flag("transport", "local") === "local") return new LocalThread(LOCAL_THREAD, author);
  const client = rt(which);
  let id = flag("thread");
  if (!id && which === "VC") {
    const t = await client.createThread(`Northbeam check-in: ${COMPANY}`, true);
    id = t.threadId;
    console.log(`\nCreated chat ${id}. Send this link to the founder; it works once and expires in 24 hours:\n  ${t.connectUrl ?? JSON.stringify(t.raw)}\n`);
  }
  if (!id) {
    const external = (await client.listThreads()).filter((t) => t.external);
    if (!external.length) throw new Error("No cross-workspace chats yet. Accept the fund's link in the Roundtable dashboard first, or pass --thread.");
    id = String(external[0].thread_id ?? external[0].id);
    console.log(`Using chat ${id} (${external[0].title ?? "untitled"}). Pass --thread to pick another.`);
  }
  return new RoundtableThread(client, id);
}

async function main() {
  if (side === "demo") {
    // Both sides in one process over a fresh local thread.
    const { rmSync } = await import("node:fs");
    rmSync(LOCAL_THREAD, { force: true });
    const stop = new AbortController();
    const startup = runStartup(new LocalThread(LOCAL_THREAD, "startup"), async () => loadDataroom(DATAROOM), approvalMode(), { signal: stop.signal })
      .catch((e) => { if (!stop.signal.aborted) throw e; });
    const { reportPath } = await runCheckin(new LocalThread(LOCAL_THREAD, "vc"), FUND_DIR, COMPANY);
    stop.abort();
    await startup;
    console.log(`\nReport: ${reportPath}`);
    return;
  }
  if (side === "vc" && cmd === "run") {
    const { reportPath } = await runCheckin(await pickThread("VC", "vc"), FUND_DIR, COMPANY, { followUp: flag("no-follow-up") !== "true" });
    console.log(`\nReport: ${reportPath}`);
    if (flag("transport") === "roundtable" && flag("save-to-bank") === "true") {
      const { readFileSync } = await import("node:fs");
      await rt("VC").writeFile(`reports/${COMPANY}.md`, readFileSync(reportPath, "utf8"), `Check-in report for ${COMPANY}`, flag("bank"));
      console.log(`Saved to the fund's Roundtable bank at reports/${COMPANY}.md`);
    }
    return;
  }
  if (side === "vc" && cmd === "threads") return console.log(JSON.stringify(await rt("VC").listThreads(), null, 2));
  if (side === "startup" && cmd === "threads") return console.log(JSON.stringify(await rt("STARTUP").listThreads(), null, 2));
  if (side === "startup" && cmd === "sync") return syncDataroom(rt("STARTUP"), DATAROOM, flag("bank"), flag("prefix", "dataroom")!);
  if (side === "startup" && cmd === "run") {
    const fromBank = flag("from-bank") === "true";
    const docs = fromBank ? () => bankDataroom(rt("STARTUP"), flag("bank"), flag("prefix", "dataroom")!) : async () => loadDataroom(DATAROOM);
    return runStartup(await pickThread("STARTUP", "startup"), docs, approvalMode(), { rounds: Number(flag("rounds", "Infinity")) });
  }
  console.log(`Usage:
  npm run demo [-- --approve-all] [--withhold s7]       both agents on this machine
  npm run vc -- run [--transport roundtable] [--thread ID] [--save-to-bank]
  npm run vc -- threads                                 list the fund's Roundtable chats
  npm run startup -- sync [--bank NAME]                 copy the data room into the startup's Roundtable bank
  npm run startup -- run [--transport roundtable] [--thread ID] [--from-bank] [--approve-all] [--withhold s7,f2]`);
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
