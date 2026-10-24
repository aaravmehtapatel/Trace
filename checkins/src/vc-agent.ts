// The fund's agent: asks the standard questions plus a few tailored to what the company said
// last quarter, checks the answers against the attached exports, follows up once on what's
// missing or weak, then writes the report.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { externalFindings, externalSignals } from "./akta.ts";
import { type Company, runChecks } from "./checks.ts";
import { callJson } from "./llm.ts";
import type { CheckinRequest, Question } from "./protocol.ts";
import { fallbackStory, narrative, renderReport, type Round } from "./report.ts";
import { PacketReader, type Thread, sendPacket } from "./transport.ts";
import { newSession } from "./tracing.ts";

const Asked = z.object({ questions: z.array(z.object({ text: z.string(), why: z.string() })).max(4) });

export function loadCompany(fundDir: string, slug: string) {
  const company = JSON.parse(readFileSync(join(fundDir, "companies", `${slug}.json`), "utf8")) as Company;
  const notes = readFileSync(join(fundDir, "companies", `${slug}.md`), "utf8");
  const standard = readFileSync(join(fundDir, "standard-questions.md"), "utf8")
    .split("\n").map((l) => /^\d+\.\s+(.*)$/.exec(l.trim())?.[1]).filter((x): x is string => !!x);
  return { company, notes, standard };
}

export async function tailoredQuestions(c: Company, notes: string, standard: string[], session: string): Promise<Question[]> {
  const out = await callJson({
    agent: "vc-agent", session, step: "plan-questions", schema: Asked,
    system: `You prepare a venture fund's quarterly check-in. The standard questions are always asked. Add 2 to 4 specific questions that follow up on what the company told the fund last quarter: commitments, deals, plans and numbers it should now be able to update. Each must be answerable from a company's own records, and must not repeat a standard question. Reply with only JSON: {"questions":[{"text":"...","why":"..."}]}.`,
    user: `Standard questions:\n${standard.map((s, i) => `${i + 1}. ${s}`).join("\n")}\n\nOur notes on ${c.name}:\n${notes}`,
  });
  return out.questions.map((q, i) => ({ id: `t${i + 1}`, text: q.text, why: q.why, kind: "tailored" }));
}

export async function followUps(c: Company, rounds: Round[], session: string): Promise<Question[]> {
  const checks = runChecks(c, rounds);
  const weak = rounds.flatMap((r) => r.resp.answers.filter((a) => a.tier !== "verified").map((a) => {
    const q = r.req.questions.find((x) => x.id === a.question_id)!;
    return `${q.id} [${a.tier}] ${q.text}\n   answer: ${a.answer}`;
  }));
  if (!weak.length && !checks.findings.some((f) => f.severity === "high")) return [];
  const out = await callJson({
    agent: "vc-agent", session, step: "follow-up", schema: Asked,
    system: `You review a startup's check-in answers for a venture fund. Write at most 3 follow-up questions on the most important gaps: unanswered or withheld items, numbers backed only by documents, and anything the computed findings contradict. Be specific and polite; quote the number you're asking about. Reply with only JSON: {"questions":[{"text":"...","why":"..."}]}.`,
    user: `Company: ${c.name}\n\nComputed findings:\n${checks.findings.map((f) => `- [${f.severity}] ${f.title}: ${f.detail}`).join("\n")}\n\nAnswers that aren't verified:\n${weak.join("\n")}`,
  });
  return out.questions.slice(0, 3).map((q, i) => ({ id: `f${i + 1}`, text: q.text, why: q.why, kind: "followup" }));
}

export type VcRun = { reportPath: string; rounds: Round[] };

export async function runCheckin(thread: Thread, fundDir: string, slug: string, opts: { followUp?: boolean; log?: (s: string) => void } = {}): Promise<VcRun> {
  const log = opts.log ?? console.log;
  const session = newSession(`vc-${slug}`);
  const { company: c, notes, standard } = loadCompany(fundDir, slug);
  const reader = new PacketReader(thread);
  const rounds: Round[] = [];

  const ask = async (questions: Question[], round: number) => {
    const req: CheckinRequest = { type: "checkin.request", id: `${slug}-${c.period}-${randomUUID().slice(0, 6)}`, round, fund: c.fund, company: c.name, period: c.period, questions };
    await sendPacket(thread, req, `${c.fund}'s check-in agent is asking ${c.name} for its ${c.period} update${round > 1 ? " (follow-up)" : ""}: ${questions.length} questions. Nothing is shared until a founder approves it.`);
    log(`[vc] sent round ${round}: ${questions.length} questions. Waiting for the founder's approved reply...`);
    const resp = await reader.next("checkin.response");
    if (resp.request_id !== req.id) throw new Error(`reply was for ${resp.request_id}, expected ${req.id}`);
    rounds.push({ req, resp });
    log(`[vc] round ${round} reply approved by ${resp.approved_by}: ${tiers(resp.answers.map((a) => a.tier))}`);
  };

  log(`[vc] planning ${c.period} questions for ${c.name}...`);
  const tailored = await tailoredQuestions(c, notes, standard, session);
  await ask([...standard.map((text, i): Question => ({ id: `s${i + 1}`, text, kind: "standard" })), ...tailored], 1);

  if (opts.followUp !== false) {
    const more = await followUps(c, rounds, session);
    if (more.length) await ask(more, 2);
  }

  const ext = await externalSignals(c, fundDir);
  const checks = runChecks(c, rounds);
  checks.findings.push(...externalFindings(ext));
  log(`[vc] ${checks.findings.filter((f) => f.severity === "high").length} high and ${checks.findings.filter((f) => f.severity === "medium").length} medium flags. Writing the report...`);
  const story = await narrative(c, notes, rounds, checks, ext, session).catch((e) => {
    log(`[vc] the model's summary failed (${String(e).slice(0, 120)}); using the computed findings instead.`);
    return fallbackStory(checks);
  });
  const dir = join(fundDir, "reports");
  mkdirSync(dir, { recursive: true });
  const reportPath = join(dir, `${slug}-${c.period}.md`);
  writeFileSync(reportPath, renderReport(c, rounds, checks, ext, story));
  writeFileSync(join(dir, `${slug}-${c.period}.json`), JSON.stringify({ company: c, rounds, checks, external: ext, story }, null, 2));
  return { reportPath, rounds };
}

function tiers(ts: string[]) {
  const count = (t: string) => ts.filter((x) => x === t).length;
  return `${count("verified")} verified, ${count("documented")} documented, ${count("unanswered")} unanswered, ${count("withheld")} withheld`;
}
