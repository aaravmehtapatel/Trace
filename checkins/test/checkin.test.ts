// End to end with a scripted model: both agents over a local thread, the founder withholding
// one answer. Checks the parts the model must not decide: grounding, tiers, policy, the math.
import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { loadDataroom, quoteFound } from "../src/dataroom.ts";
import { useModel } from "../src/llm.ts";
import type { CheckinRequest } from "../src/protocol.ts";
import { runStartup } from "../src/startup.ts";
import { ground, NOT_COVERED } from "../src/startup-agent.ts";
import { LocalThread, PacketReader, sendPacket } from "../src/transport.ts";
import { runCheckin } from "../src/vc-agent.ts";

const ROOT = resolve(import.meta.dirname, "..");
const ROOM = join(ROOT, "dataroom", "tallyline");

const CITES: Record<string, { answer: string; sources: { path: string; quote: string }[] }> = {
  s1: { answer: "MRR was $66,000 in July, $67,500 in August and $68,000 in September.", sources: [{ path: "systems/billing_mrr_monthly.csv", quote: "2026-09,68000,7400,900,283" }] },
  s2: { answer: "Net burn rose to $214,000 in September; cash was $2,347,000.", sources: [{ path: "systems/bank_cash_monthly.csv", quote: "2026-09,2561000,68000,0,208700,73300,2347000" }] },
  s3: { answer: "The runway model says about 22 months.", sources: [{ path: "finance/runway-model.md", quote: "Runway: about 22 months at $140k per month" }] },
  s4: { answer: NOT_COVERED, sources: [] },
  s5: { answer: "15 people on 2026-09-01, down from 17.", sources: [{ path: "company/team.md", quote: "Marcus Bell, Head of Sales, left 2026-08-29." }] },
  // A made-up quote: grounding must drop it and mark the answer unanswered.
  s6: { answer: "We beat every target.", sources: [{ path: "strategy/2026-plan.md", quote: "we beat every Q3 target" }] },
  s7: { answer: "Toast sync errors and the bank pilot pause.", sources: [{ path: "product/incidents.md", quote: "Our sales sync dropped about 30% of orders for Toast customers." }] },
  s8: { answer: NOT_COVERED, sources: [] },
  t1: { answer: "Meridian paused the pilot until SOC 2 Type II is in place.", sources: [{ path: "customers/pipeline-notes.md", quote: "Meridian paused the pilot \"until SOC 2 Type II is in place\"." }] },
  f1: { answer: NOT_COVERED, sources: [] },
};

function scriptedModel(seenStartupPrompts: string[]) {
  return async (system: string, user: string) => {
    const reply = (o: unknown) => ({ text: JSON.stringify(o), model: "scripted" });
    if (system.includes("check-in agent for a startup")) {
      seenStartupPrompts.push(user);
      const ids = [...user.matchAll(/^([stf]\d+)\. /gm)].map((m) => m[1]);
      return reply({ answers: ids.map((id) => ({ question_id: id, ...(CITES[id] ?? { answer: NOT_COVERED, sources: [] }) })) });
    }
    if (system.includes("prepare a venture fund")) return reply({ questions: [{ text: "Did the Meridian pilot launch in September?", why: "Promised in Q2." }] });
    if (system.includes("review a startup's check-in")) return reply({ questions: [{ text: "What was monthly revenue churn in July, August and September?", why: "Reported in Q1 and Q2, missing now." }] });
    if (system.includes("analyst writing")) return reply({ status: "watch", bottom_line: "Runway is half what was claimed.", board_questions: ["What is the bridge plan?", "Why did churn stop being reported?"] });
    throw new Error(`unexpected prompt: ${system.slice(0, 80)}`);
  };
}

test("quotes must appear in the cited file", () => {
  const docs = loadDataroom(ROOM);
  const plan = docs.find((d) => d.path === "strategy/2026-plan.md");
  assert.ok(quoteFound(plan, "| Q3 (Sep) | $80,000 | 340 | $150,000 |"));
  assert.ok(!quoteFound(plan, "we beat every Q3 target"));
  assert.ok(!docs.some((d) => d.path.startsWith("people/")), "people/ is never loaded");
});

test("tiers come from the kind of file cited", () => {
  const docs = loadDataroom(ROOM);
  const req: CheckinRequest = { type: "checkin.request", id: "r", round: 1, fund: "F", company: "C", period: "P",
    questions: ["s1", "s3", "s4", "s6"].map((id) => ({ id, text: id, kind: "standard" as const })) };
  const r = ground(req, ["s1", "s3", "s4", "s6"].map((id) => ({ question_id: id, ...CITES[id] })), docs);
  assert.deepEqual(r.answers.map((a) => a.tier), ["verified", "documented", "unanswered", "unanswered"]);
  assert.deepEqual(r.attachments.map((a) => a.path), ["systems/billing_mrr_monthly.csv"]);
});

test("large packets survive the 8,000-character post limit", async () => {
  const dir = mkdtempSync(join(tmpdir(), "checkins-"));
  const a = new LocalThread(join(dir, "t.jsonl"), "a");
  const b = new LocalThread(join(dir, "t.jsonl"), "b");
  const req: CheckinRequest = { type: "checkin.request", id: "big", round: 1, fund: "F", company: "C", period: "P",
    questions: Array.from({ length: 60 }, (_, i) => ({ id: `q${i}`, text: "x".repeat(300), kind: "standard" as const })) };
  await sendPacket(a, req, "headline");
  const got = await new PacketReader(b).next("checkin.request", { pollMs: 10 });
  assert.equal(got.questions.length, 60);
  const posts = readFileSync(join(dir, "t.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l).text as string);
  assert.ok(posts.length > 3 && posts.every((p) => p.length <= 8000));
});

test("a full check-in, founder withholds one answer", async () => {
  const dir = mkdtempSync(join(tmpdir(), "checkins-"));
  const fundDir = join(dir, "fund");
  cpSync(join(ROOT, "fund", "northbeam"), fundDir, { recursive: true });
  const prompts: string[] = [];
  useModel(scriptedModel(prompts));
  try {
    const file = join(dir, "thread.jsonl");
    const stop = new AbortController();
    const startup = runStartup(new LocalThread(file, "startup"), async () => loadDataroom(ROOM),
      { interactive: false, withhold: new Set(["s7"]), approver: "Dana Okafor", outbox: join(dir, "outbox") },
      { signal: stop.signal, log: () => {} }).catch(() => {});
    const { reportPath, rounds } = await runCheckin(new LocalThread(file, "vc"), fundDir, "tallyline", { log: () => {} });
    stop.abort();
    await startup;

    assert.equal(rounds.length, 2, "one follow-up round");
    const r1 = rounds[0].resp.answers;
    const tier = (id: string) => r1.find((a) => a.question_id === id)!.tier;
    assert.equal(tier("s1"), "verified");
    assert.equal(tier("s6"), "unanswered", "a fabricated quote is dropped");
    assert.equal(tier("s7"), "withheld");
    assert.equal(rounds[0].resp.approved_by, "Dana Okafor");
    assert.ok(prompts.every((p) => !p.includes("CONFIDENTIAL") && !p.includes("Jo Alvarez | Head of CS")), "compensation never reaches the model");

    const report = readFileSync(reportPath, "utf8");
    assert.match(report, /\*\*Concern\*\*/, "a high finding raises the model's 'watch' to Concern");
    assert.match(report, /Runway is 11 months, not 22/);
    assert.match(report, /Churn stopped being reported, and billing shows it at 4x last quarter/);
    assert.match(report, /9\.7%/, "Q3 implied churn average");
    assert.match(report, /Headcount down from 17 to 15 \(akta\)/);
    assert.match(report, /Withheld: Top risks right now/);
  } finally {
    useModel(undefined);
  }
});
