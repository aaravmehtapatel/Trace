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

export async function approve(req: CheckinRequest, draft: CheckinResponse, docs: Doc[], mode: ApprovalMode): Promise<CheckinResponse> {
  mkdirSync(mode.outbox, { recursive: true });
  const file = join(mode.outbox, `${req.id}-round${req.round}.draft.md`);
  writeFileSync(file, draftMarkdown(req, draft));
  console.log(`\nDraft written to ${file}`);

  const answers = draft.answers.map((a) => ({ ...a, sources: [...a.sources] }));
  const rl = mode.interactive ? createInterface({ input: process.stdin, output: process.stdout }) : undefined;
  let approveRest = false;
  try {
    for (const a of answers) {
      const q = req.questions.find((x) => x.id === a.question_id)!;
      if (mode.withhold.has(a.question_id)) {
        withhold(a);
        console.log(`  ${a.question_id}: withheld (--withhold)`);
        continue;
      }
      if (!rl || approveRest) continue;
      console.log(`\n${q.id}. ${q.text}\n  [${a.tier}] ${a.answer}`);
      for (const s of a.sources) console.log(`    - ${s.path}: "${s.quote}"`);
      const choice = (await rl.question("  Enter = approve, e = edit, w = withhold, A = approve all the rest: ")).trim();
      if (choice === "A") approveRest = true;
      else if (choice === "w") withhold(a);
      else if (choice === "e") {
        const text = (await rl.question("  New answer: ")).trim();
        if (text) Object.assign(a, { answer: text, founder_edited: true });
      }
    }
  } finally {
    rl?.close();
  }
  const byPath = new Map(docs.map((d) => [d.path, d]));
  return { ...draft, answers, attachments: attachmentsFor(answers, byPath), approved_by: mode.approver, approved_at: new Date().toISOString() };
}

function withhold(a: CheckinResponse["answers"][number]) {
  Object.assign(a, { answer: "Withheld by the founder.", tier: "withheld", sources: [], founder_edited: true });
}
