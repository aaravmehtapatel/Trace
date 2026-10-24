// Outside signals from akta (Wokelo): news and monthly headcount, to set what the company says
// against what the world can see. Same endpoints as the census (src/ingest/akta.py). A
// synthetic company has no real domain, so it uses a labelled sample file instead.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Company, Finding } from "./checks.ts";

const API = "https://api.akta.pro/api/v1";
const NEGATIVE = /layoff|laid off|lay off|shut(s|ting)? down|wind(s)? down|bankrupt|insolven|ceases operations|acquired by/i;

export type External = {
  source: "akta" | "akta-sample" | "none";
  headcount?: { monthly: Record<string, number>; growth_6?: number };
  news: { date: string; title: string; url?: string; warning: boolean }[];
  note?: string;
};

async function get(path: string, params: Record<string, string>): Promise<Record<string, unknown> | undefined> {
  const url = `${API}${path}?${new URLSearchParams(params)}`;
  const res = await fetch(url, { headers: { "x-api-key": process.env.AKTA_API_KEY!, Accept: "application/json" }, signal: AbortSignal.timeout(40_000) });
  return res.ok ? ((await res.json()) as Record<string, unknown>) : undefined; // 403: not on this plan
}

export async function externalSignals(c: Company, fundDir: string): Promise<External> {
  if (c.domain && process.env.AKTA_API_KEY) {
    const news = ((await get("/news", { company: `https://${c.domain}`, limit: "50" }))?.data as Record<string, string>[] | undefined) ?? [];
    const search = ((await get("/company/search/", { query: c.domain }))?.data as Record<string, string>[] | undefined) ?? [];
    const match = search.find((x) => (x.website ?? "").toLowerCase().split("//").pop()!.replace(/^www\./, "").replace(/\/$/, "") === c.domain);
    let headcount: External["headcount"];
    if (match?.uuid) {
      const d = ((await get("/company/headcount-trends", { company: match.uuid }))?.data ?? {}) as Record<string, unknown>;
      const monthly = Object.fromEntries(((d.headcount_monthly as { date: string; employee_count: number }[]) ?? []).map((m) => [m.date.slice(0, 7), m.employee_count]));
      const g6 = ((d.growth_periods as { month_difference: number; change_percentage: number }[]) ?? []).find((g) => g.month_difference === 6);
      if (Object.keys(monthly).length) headcount = { monthly, growth_6: g6?.change_percentage };
    }
    return { source: "akta", headcount, news: news.map((a) => ({ date: (a.published_date ?? "").slice(0, 10), title: a.title ?? "", url: a.url,
      warning: NEGATIVE.test(`${a.title} ${a.ai_summary ?? ""}`) })).sort((x, y) => y.date.localeCompare(x.date)).slice(0, 8) };
  }
  if (c.akta_sample && existsSync(join(fundDir, c.akta_sample))) {
    const s = JSON.parse(readFileSync(join(fundDir, c.akta_sample), "utf8"));
    return { source: "akta-sample", headcount: s.headcount, news: s.news ?? [], note: "Synthetic sample: this company is invented, so there is no live akta record." };
  }
  return { source: "none", news: [], note: c.domain ? "Set AKTA_API_KEY to pull live news and headcount." : "No domain on file." };
}

export function externalFindings(e: External): Finding[] {
  const out: Finding[] = [];
  const months = Object.keys(e.headcount?.monthly ?? {}).sort();
  if (months.length >= 4) {
    const m = e.headcount!.monthly;
    const peak = Math.max(...months.slice(-4).map((k) => m[k]));
    const now = m[months[months.length - 1]];
    if (now < peak) out.push({ severity: "medium", title: `Headcount down from ${peak} to ${now} (akta)`,
      detail: `Outside headcount data shows the team shrinking since ${months.find((k) => m[k] === peak)}, while six hires were announced for H2.`, evidence: [e.source === "akta" ? "akta headcount-trends" : "akta sample (synthetic)"] });
  }
  for (const n of e.news.filter((x) => x.warning)) out.push({ severity: "high", title: `News: ${n.title}`, detail: n.date, evidence: [n.url ?? "akta news"] });
  return out;
}
