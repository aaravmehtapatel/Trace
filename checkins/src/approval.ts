// The founder gate. Nothing leaves the startup until a person approves it; they can edit an
// answer or withhold it. A withheld answer is sent as withheld, not dropped, so the fund
// knows a question was seen and declined rather than missed.
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline/promises";
import type { Doc } from "./dataroom.ts";
import type { CheckinRequest, CheckinResponse } from "./protocol.ts";
import { attachmentsFor } from "./startup-agent.ts";

export type ApprovalMode = { interactive: boolean; withhold: Set<string>; approver: string; outbox: string };
export type Decision = { action: "approve" | "edit" | "withhold"; text?: string };
// Anything that turns a draft into an approved reply: the terminal prompt below, or the web page.
export type Approver = (req: CheckinRequest, draft: CheckinResponse, docs: Doc[]) => Promise<CheckinResponse>;

export function draftMarkdown(req: CheckinRequest, draft: CheckinResponse): string {
  const lines = [`# Draft reply to ${req.fund}: ${req.period} check-in, round ${req.round}`, "",
    "Nothing has been sent. Approve, edit or withhold each answer.", ""];
  for (const q of req.questions) {
    const a = draft.answers.find((x) => x.question_id === q.id)!;
    lines.push(`## ${q.id}. ${q.text}`, "", `**${a.tier.toUpperCase()}**: ${a.answer}`, "");
    for (const s of a.sources) lines.push(`- \`${s.path}\` @${s.version}: "${s.quote}"`);
    lines.push("");
  }
  return lines.join("\n");
}

export function finalize(draft: CheckinResponse, docs: Doc[], decisions: Record<string, Decision>, approver: string): CheckinResponse {
  const answers = draft.answers.map((a) => {
    const d = decisions[a.question_id];
    if (d?.action === "withhold") return { ...a, answer: "Withheld by the founder.", tier: "withheld" as const, sources: [], founder_edited: true };
    if (d?.action === "edit" && d.text?.trim()) return { ...a, answer: d.text.trim(), founder_edited: true };
    return a;
  });
  const byPath = new Map(docs.map((d) => [d.path, d]));
  return { ...draft, answers, attachments: attachmentsFor(answers, byPath), approved_by: approver, approved_at: new Date().toISOString() };
}

export function terminalApprover(mode: ApprovalMode): Approver {
  return async (req, draft, docs) => {
    mkdirSync(mode.outbox, { recursive: true });
    const file = join(mode.outbox, `${req.id}-round${req.round}.draft.md`);
    writeFileSync(file, draftMarkdown(req, draft));
    console.log(`\nDraft written to ${file}`);

    const decisions: Record<string, Decision> = {};
    const rl = mode.interactive ? createInterface({ input: process.stdin, output: process.stdout }) : undefined;
    let approveRest = false;
    try {
      for (const a of draft.answers) {
        const q = req.questions.find((x) => x.id === a.question_id)!;
        if (mode.withhold.has(a.question_id)) {
          decisions[a.question_id] = { action: "withhold" };
          console.log(`  ${a.question_id}: withheld (--withhold)`);
          continue;
        }
        if (!rl || approveRest) continue;
        console.log(`\n${q.id}. ${q.text}\n  [${a.tier}] ${a.answer}`);
        for (const s of a.sources) console.log(`    - ${s.path}: "${s.quote}"`);
        const choice = (await rl.question("  Enter = approve, e = edit, w = withhold, A = approve all the rest: ")).trim();
        if (choice === "A") approveRest = true;
        else if (choice === "w") decisions[a.question_id] = { action: "withhold" };
        else if (choice === "e") decisions[a.question_id] = { action: "edit", text: await rl.question("  New answer: ") };
      }
    } finally {
      rl?.close();
    }
    return finalize(draft, docs, decisions, mode.approver);
  };
}
