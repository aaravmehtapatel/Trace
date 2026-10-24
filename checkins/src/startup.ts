// The startup side's loop: wait for a request, draft from the data room, get the founder's
// approval, send. The data room is a local folder or the company's Roundtable bank.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { type ApprovalMode, type Approver, terminalApprover } from "./approval.ts";
import { type Doc, loadDataroom } from "./dataroom.ts";
import { RoundtableHttp } from "./roundtable.ts";
import { draftResponse } from "./startup-agent.ts";
import { newSession } from "./tracing.ts";
import { PacketReader, type Thread, sendPacket } from "./transport.ts";

export async function runStartup(thread: Thread, docs: () => Promise<Doc[]>, mode: ApprovalMode,
  opts: { rounds?: number; signal?: AbortSignal; log?: (s: string) => void; approver?: Approver } = {}): Promise<void> {
  const log = opts.log ?? console.log;
  const approve = opts.approver ?? terminalApprover(mode);
  const reader = new PacketReader(thread);
  const session = newSession("startup");
  for (let i = 0; i < (opts.rounds ?? Infinity); i++) {
    log("[startup] waiting for a check-in request...");
    const req = await reader.next("checkin.request", { timeoutMs: 24 * 3600_000, signal: opts.signal });
    log(`[startup] ${req.fund} asked ${req.questions.length} questions (round ${req.round}). Drafting from the data room...`);
    const room = await docs();
    const draft = await draftResponse(req, room, session);
    const resp = await approve(req, draft, room);
    const shared = resp.answers.filter((a) => a.tier !== "unanswered" && a.tier !== "withheld").length;
    await sendPacket(thread, resp, `${req.company}'s agent replied to ${req.fund}, approved by ${resp.approved_by}: ${shared} of ${resp.answers.length} answered with citations.`);
    log(`[startup] sent round ${req.round}, approved by ${resp.approved_by}.`);
  }
}

// The data room as stored in a Roundtable bank, so citations carry Roundtable's own versions.
export async function bankDataroom(rt: RoundtableHttp, bank: string | undefined, prefix: string): Promise<Doc[]> {
  const files = (await rt.listFiles(prefix, bank)).filter((f) => /\.(md|csv)$/.test(f.path));
  const docs: Doc[] = [];
  for (const f of files) {
    const path = f.path.slice(prefix.length).replace(/^\//, "");
    if (path.startsWith("people/")) continue;
    const r = await rt.readFile(f.path, bank);
    const content = String(r.content ?? "");
    const src = (r.source ?? {}) as { version?: string };
    const version = String(src.version ?? r.version ?? f.version ?? createHash("sha256").update(content).digest("hex").slice(0, 8));
    docs.push({ path, version, content, system: path.startsWith("systems/") });
  }
  return docs;
}

export async function syncDataroom(rt: RoundtableHttp, localRoot: string, bank: string | undefined, prefix: string, log = console.log) {
  for (const d of loadDataroom(localRoot, { includeConfidential: true })) {
    await rt.writeFile(`${prefix}/${d.path}`, readFileSync(`${localRoot}/${d.path}`, "utf8"), `Sync ${d.path} from the local data room`, bank);
    log(`  uploaded ${prefix}/${d.path}`);
  }
}
