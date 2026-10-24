// Deterministic checks the fund runs on what came back. The model never decides these: they
// are recomputed from the raw billing and bank exports the startup attached.
import type { CheckinRequest, CheckinResponse } from "./protocol.ts";

export type Company = {
  slug: string; name: string; fund: string; period: string; domain: string | null; next_board_meeting: string;
  last_reported: { period: string; mrr: number; monthly_churn_pct: number; net_burn_per_month: number; runway_months: number; customers: number };
  plan: { mrr: number; net_burn_per_month: number; customers: number };
  akta_sample?: string;
};

export type Severity = "high" | "medium" | "low";
export type Finding = { severity: Severity; title: string; detail: string; evidence: string[] };
export type Metric = { name: string; last: string; now: string; plan?: string; basis: string };
export type Rows = Record<string, string>[];

export function parseCsv(text: string): Rows {
  const [head, ...lines] = text.trim().split(/\r?\n/);
  const cols = head.split(",");
  return lines.filter(Boolean).map((l) => Object.fromEntries(l.split(",").map((v, i) => [cols[i], v])));
}

const n = (v: string | undefined) => Number(v);
const usd = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`;
const pct = (v: number) => `${v.toFixed(1)}%`;

function exportWith(resp: CheckinResponse, ...cols: string[]) {
  for (const a of resp.attachments) {
    const rows = parseCsv(a.content);
    if (rows.length && cols.every((c) => c in rows[0])) return { rows, path: a.path, version: a.version };
  }
  return undefined;
}

export type CheckResult = { findings: Finding[]; metrics: Metric[] };

export function runChecks(c: Company, rounds: { req: CheckinRequest; resp: CheckinResponse }[]): CheckResult {
  const findings: Finding[] = [];
  const metrics: Metric[] = [];
  const all = rounds.flatMap((r) => r.resp.answers.map((a) => ({ a, q: r.req.questions.find((q) => q.id === a.question_id)! })));
  const latest = rounds[rounds.length - 1].resp;
  const merged: CheckinResponse = { ...latest, attachments: dedupe(rounds.flatMap((r) => r.resp.attachments)) };

  const billing = exportWith(merged, "mrr", "new_mrr", "expansion_mrr");
  const bank = exportWith(merged, "closing_cash", "revenue_collected");

  if (billing) {
    const rows = billing.rows;
    const last = rows[rows.length - 1];
    const cite = `${billing.path} @${billing.version}`;
    metrics.push({ name: `MRR (${last.month})`, last: usd(c.last_reported.mrr), now: usd(n(last.mrr)), plan: usd(c.plan.mrr), basis: `verified: ${cite}` });
    const miss = 1 - n(last.mrr) / c.plan.mrr;
    if (miss > 0.1) findings.push({ severity: "medium", title: `MRR ${pct(miss * 100)} below plan`,
      detail: `${usd(n(last.mrr))} in ${last.month} against the ${usd(c.plan.mrr)} plan. Growth over the last quarter was ${pct(100 * (n(last.mrr) / n(rows[rows.length - 4]?.mrr ?? last.mrr) - 1))}.`, evidence: [cite] });

    // Churn the founder didn't report can still be read off billing: what the base lost is
    // last month's MRR plus new and expansion, minus this month's.
    const churn = rows.slice(1).map((r, i) => ({ month: r.month, pct: (100 * (n(rows[i].mrr) + n(r.new_mrr) + n(r.expansion_mrr) - n(r.mrr))) / n(rows[i].mrr) }));
    const q = churn.slice(-3);
    const avg = q.reduce((s, x) => s + x.pct, 0) / q.length;
    const churnQ = all.find(({ q }) => /churn/i.test(q.text));
    const silent = churnQ && ["unanswered", "withheld"].includes(churnQ.a.tier);
    metrics.push({ name: "Monthly revenue churn", last: pct(c.last_reported.monthly_churn_pct), now: pct(avg),
      basis: `computed from ${cite}${silent ? "; not reported by the company" : ""}` });
    if (avg > 2 * c.last_reported.monthly_churn_pct) findings.push({ severity: "high",
      title: silent ? `Churn stopped being reported, and billing shows it at ${(avg / c.last_reported.monthly_churn_pct).toFixed(0)}x last quarter`
        : `Billing shows churn at ${(avg / c.last_reported.monthly_churn_pct).toFixed(0)}x last quarter`,
      detail: `Billing implies revenue churn of ${q.map((x) => `${pct(x.pct)} (${x.month})`).join(", ")}, an average of ${pct(avg)} a month, against the ${pct(c.last_reported.monthly_churn_pct)} reported for ${c.last_reported.period}.${silent ? ` The company ${churnQ!.a.tier === "withheld" ? "withheld" : "didn't answer"} the churn question (${churnQ!.q.id}).` : ""}`,
      evidence: [cite, ...(churnQ ? [`answer ${churnQ.q.id}: ${churnQ.a.tier}`] : [])] });

    const cust = rows.map((r) => n(r.active_customers));
    const peak = Math.max(...cust.slice(-4));
    metrics.push({ name: "Active customers", last: String(c.last_reported.customers), now: String(cust[cust.length - 1]), plan: String(c.plan.customers), basis: `verified: ${cite}` });
    if (cust[cust.length - 1] < peak) findings.push({ severity: "medium", title: "Customer count is falling",
      detail: `Down from ${peak} to ${cust[cust.length - 1]} over the quarter, while the plan called for ${c.plan.customers}.`, evidence: [cite] });
  }

  if (bank) {
    const rows = bank.rows;
    const cite = `${bank.path} @${bank.version}`;
    const burn = rows.map((r) => ({ month: r.month, v: n(r.payroll_out) + n(r.other_out) - n(r.revenue_collected), cash: n(r.closing_cash) }));
    const last = burn[burn.length - 1];
    const runway = last.cash / last.v;
    metrics.push({ name: `Net burn (${last.month})`, last: `${usd(c.last_reported.net_burn_per_month)}/mo`, now: `${usd(last.v)}/mo`, plan: `${usd(c.plan.net_burn_per_month)}/mo`, basis: `verified: ${cite}` });
    metrics.push({ name: `Cash (${last.month})`, last: "", now: usd(last.cash), basis: `verified: ${cite}` });
    metrics.push({ name: "Runway at current burn", last: `${c.last_reported.runway_months} months (company's claim)`, now: `${runway.toFixed(1)} months`, basis: `computed from ${cite}` });
    if (runway < 0.75 * c.last_reported.runway_months) findings.push({ severity: "high", title: `Runway is ${runway.toFixed(0)} months, not ${c.last_reported.runway_months}`,
      detail: `${usd(last.cash)} cash at ${usd(last.v)} net burn in ${last.month} is ${runway.toFixed(1)} months. Last quarter the company said ~${c.last_reported.runway_months}. Burn went from ${usd(burn[burn.length - 4]?.v ?? last.v)} to ${usd(last.v)} in three months.`, evidence: [cite] });
    if (last.v > 1.2 * c.plan.net_burn_per_month) findings.push({ severity: "medium", title: `Burn ${pct(100 * (last.v / c.plan.net_burn_per_month - 1))} over plan`,
      detail: `${usd(last.v)} in ${last.month} against a plan of ${usd(c.plan.net_burn_per_month)}.`, evidence: [cite] });
  }

  for (const { a, q } of all) {
    if (a.tier === "unanswered" || a.tier === "withheld")
      findings.push({ severity: q.kind === "followup" ? "medium" : "low", title: `${a.tier === "withheld" ? "Withheld" : "Unanswered"}: ${q.text.replace(/\.$/, "")}`,
        detail: a.tier === "withheld" ? "The founder declined to share this." : "The data room has nothing that answers it.", evidence: [`answer ${q.id}`] });
  }
  if (!billing && !bank) findings.push({ severity: "medium", title: "No system-of-record data shared", detail: "Every number this quarter is self-reported.", evidence: [] });

  const rank: Record<Severity, number> = { high: 0, medium: 1, low: 2 };
  findings.sort((x, y) => rank[x.severity] - rank[y.severity]);
  return { findings, metrics };
}

function dedupe<T extends { path: string; version: string }>(xs: T[]): T[] {
  const seen = new Set<string>();
  return xs.filter((x) => !seen.has(x.path + x.version) && seen.add(x.path + x.version));
}
