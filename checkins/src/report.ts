// The fund's one-page report. Structure, numbers and flags are built in code; the model only
// writes the bottom line and the board questions, and can't rate a company better than its
// findings allow.
import { z } from "zod";
import type { External } from "./akta.ts";
import type { CheckResult, Company, Finding } from "./checks.ts";
import { callJson } from "./llm.ts";
import type { CheckinRequest, CheckinResponse, Tier } from "./protocol.ts";

export type Status = "on_track" | "watch" | "concern";
const LABEL: Record<Status, string> = { on_track: "On track", watch: "Watch", concern: "Concern" };
const TIER: Record<Tier, string> = { verified: "Verified", documented: "Documented", unanswered: "Unanswered", withheld: "Withheld" };

const Narrative = z.object({
  status: z.enum(["on_track", "watch", "concern"]),
  bottom_line: z.string(),
  board_questions: z.array(z.string()).min(1),
});

export type Story = { status: Status; bottom_line: string; board_questions: string[] };

// Used when the model call fails, so a check-in always ends with a report.
export function fallbackStory(checks: CheckResult): Story {
  const top = checks.findings.filter((f) => f.severity !== "low");
  return {
    status: floorStatus(checks.findings),
    bottom_line: top.length ? `${top.slice(0, 3).map((f) => f.title).join(". ")}.` : "No red flags in this check-in.",
    board_questions: top.slice(0, 5).map((f) => `${f.title}: what is the plan?`),
  };
}

export function floorStatus(findings: Finding[]): Status {
  return findings.some((f) => f.severity === "high") ? "concern" : findings.some((f) => f.severity === "medium") ? "watch" : "on_track";
}

export async function narrative(c: Company, notes: string, rounds: Round[], checks: CheckResult, ext: External, session: string) {
  const qa = rounds.flatMap((r) => r.req.questions.map((q) => {
    const a = r.resp.answers.find((x) => x.question_id === q.id)!;
    return `${q.id} [${a.tier}] Q: ${q.text}\nA: ${a.answer}`;
  })).join("\n\n");
  const out = await callJson({
    agent: "vc-agent", session, step: "report", schema: Narrative,
    system: `You are a venture partner's analyst writing the top of a one-page portfolio report. Be direct and specific; name numbers. Don't repeat the findings list; say what it adds up to and what the partner should do before the board meeting. Treat "unanswered" and "withheld" answers as information. Reply with only JSON: {"status":"on_track|watch|concern","bottom_line":"3 to 5 sentences","board_questions":["at most 6"]}.`,
    user: `Company: ${c.name}. Period: ${c.period}. Next board meeting: ${c.next_board_meeting}.\n\nFund notes:\n${notes}\n\nFindings (computed, trust these):\n${checks.findings.map((f) => `- [${f.severity}] ${f.title}: ${f.detail}`).join("\n")}\n\nMetrics:\n${checks.metrics.map((m) => `- ${m.name}: ${m.now} (last ${m.last || "n/a"}${m.plan ? `, plan ${m.plan}` : ""})`).join("\n")}\n\nOutside signals (${ext.source}): ${JSON.stringify({ headcount: ext.headcount, news: ext.news })}\n\nThe company's answers:\n${qa}`,
  });
  const floor = floorStatus(checks.findings);
  const order: Status[] = ["on_track", "watch", "concern"];
  return { ...out, board_questions: out.board_questions.slice(0, 6), status: order[Math.max(order.indexOf(out.status), order.indexOf(floor))] };
}

export type Round = { req: CheckinRequest; resp: CheckinResponse };

export function renderReport(c: Company, rounds: Round[], checks: CheckResult, ext: External, story: { status: Status; bottom_line: string; board_questions: string[] }): string {
  const resp = rounds[rounds.length - 1].resp;
  const tally = (t: Tier) => rounds.flatMap((r) => r.resp.answers).filter((a) => a.tier === t).length;
  const L: string[] = [];
  L.push(`# ${c.name}: ${c.period} check-in`, "");
  L.push(`**${LABEL[story.status]}** · ${c.fund} · answers approved by ${resp.approved_by} on ${resp.approved_at.slice(0, 10)} · ${rounds.length} round${rounds.length > 1 ? "s" : ""} · next board meeting ${c.next_board_meeting}`, "");
  L.push(`Answers: ${tally("verified")} verified from systems of record, ${tally("documented")} documented, ${tally("unanswered")} unanswered, ${tally("withheld")} withheld.`, "");
  L.push("## Bottom line", "", story.bottom_line, "");

  const flags = checks.findings.filter((f) => f.severity !== "low");
  if (flags.length) {
    L.push("## Red flags", "");
    for (const f of flags) L.push(`- **${f.severity === "high" ? "High" : "Medium"}: ${f.title}.** ${f.detail}${f.evidence.length ? ` _(${f.evidence.join("; ")})_` : ""}`);
    L.push("");
  }

  if (checks.metrics.length) {
    L.push("## Metrics against last quarter", "", `| Metric | ${c.last_reported.period} (reported) | ${c.period} | Plan | Basis |`, "|---|---|---|---|---|");
    for (const m of checks.metrics) L.push(`| ${m.name} | ${m.last || "-"} | ${m.now} | ${m.plan ?? "-"} | ${m.basis} |`);
    L.push("");
  }

  const gaps = checks.findings.filter((f) => f.severity === "low");
  if (gaps.length) {
    L.push("## Not answered", "", ...gaps.map((f) => `- ${f.title}${/[?]$/.test(f.title) ? "" : "."} ${f.detail}`), "");
  }

  L.push("## Questions for the board meeting", "");
  story.board_questions.forEach((q, i) => L.push(`${i + 1}. ${q}`));
  L.push("");

  L.push("## Outside signals", "");
  if (ext.note) L.push(`_${ext.note}_`, "");
  const months = Object.keys(ext.headcount?.monthly ?? {}).sort();
  if (months.length) L.push(`- Headcount: ${months.slice(-6).map((m) => `${m} ${ext.headcount!.monthly[m]}`).join(", ")}`);
  L.push(ext.news.length ? ext.news.map((x) => `- ${x.date}: ${x.url ? `[${x.title}](${x.url})` : x.title}${x.warning ? " ⚠" : ""}`).join("\n") : "- No news coverage found.");
  L.push("");

  L.push("## Every answer, with its evidence", "");
  for (const r of rounds) {
    if (rounds.length > 1) L.push(`### Round ${r.req.round}${r.req.round > 1 ? " (follow-ups)" : ""}`, "");
    for (const q of r.req.questions) {
      const a = r.resp.answers.find((x) => x.question_id === q.id)!;
      L.push(`**${q.id}. ${q.text}**  `, `${TIER[a.tier]}${a.founder_edited && a.tier !== "withheld" ? " (edited by the founder)" : ""}: ${a.answer}`);
      for (const s of a.sources) L.push(`  - \`${s.path}\` @${s.version}: "${s.quote}"`);
      L.push("");
    }
  }
  L.push("---", `Generated ${new Date().toISOString()} by the ${c.fund} check-in agent. Numbers in "Metrics" and "Red flags" are recomputed from the attached exports, not taken from the company's prose.`);
  return L.join("\n");
}
