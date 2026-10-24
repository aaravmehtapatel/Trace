// Builds site/census-data.js from the newest exports/census-<date>.json, so the front page shows the
// real census. The Trace server (checkins/src/server.ts) serves this folder at "/" and the census at "/census".
// Run from the repo root after `python3 -m src.cli export`:  node site/build-data.mjs
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const exportsDir = join(root, 'exports');
const latest = readdirSync(exportsDir).filter((f) => /^census-\d{4}-\d{2}-\d{2}\.json$/.test(f)).sort().pop();
if (!latest) throw new Error('No exports/census-<date>.json found. Run `python3 -m src.cli export` first.');
const data = JSON.parse(readFileSync(join(exportsDir, latest), 'utf8'));

const has = (c, kind) => c.badges.some((b) => b.kind === kind);

// Same selection as src/report.py calls(): recently dark first, then fading, then rising.
function calls(companies, limit = 10) {
  const gone = companies.filter((c) => c.bucket === 'No Public Activity').sort((a, b) =>
    (a.rule.startsWith('Acquired') ? 0 : 1) - (b.rule.startsWith('Acquired') ? 0 : 1) ||
    (has(a, 'redirect') ? 0 : 1) - (has(b, 'redirect') ? 0 : 1) ||
    ((a.headcount || {}).total ? 0 : 1) - ((b.headcount || {}).total ? 0 : 1) ||
    a.name.localeCompare(b.name));
  const hcFading = companies.filter((c) => c.bucket === 'Fading' && (c.headcount || {}).growth_6 != null)
    .sort((a, b) => a.headcount.growth_6 - b.headcount.growth_6);
  const webFading = companies.filter((c) => c.bucket === 'Fading' && !hcFading.includes(c));
  const fading = [...hcFading, ...webFading.slice(0, 1)];
  const rising = companies.filter((c) => c.bucket === 'Active' &&
    (has(c, 'rising') || c.badges.some((b) => b.kind === 'form_d' && b.recent)))
    .sort((a, b) => ((b.headcount || {}).growth_12 || 0) - ((a.headcount || {}).growth_12 || 0));
  const out = [];
  for (const c of gone.slice(0, 3)) {
    out.push({ id: c.id, kind: 'gone', reason: c.rule,
      ask: c.rule.startsWith('Acquired') ? 'Confirm the terms and what the fund received, then update the mark.'
        : has(c, 'redirect') ? "Was this an acquisition? Confirm what happened to the fund's shares and update the mark."
        : 'Is the company still operating? If not, start the write-off.' });
  }
  for (const c of fading.slice(0, 4)) {
    const warn = (c.news || []).find((n) => n.warning);
    const eng = (((c.headcount || {}).functions) || {}).Engineering || {};
    const detail = eng.growth_6 != null ? `; engineering ${eng.growth_6 > 0 ? '+' : ''}${eng.growth_6}%` : '';
    out.push({ id: c.id, kind: 'fading', reason: c.rule + detail + (warn ? `. In the news: ${warn.title}` : ''),
      ask: c.headcount ? 'Who left, and what changed: the team, the product focus, or runway?'
        : 'What changed this quarter: the team, the product focus, or runway?' });
  }
  for (const c of rising.slice(0, limit - out.length)) {
    const filed = c.badges.find((b) => b.kind === 'form_d' && b.recent);
    const hc = c.headcount || {};
    const grew = hc.growth_12 ? `Headcount ${hc.growth_12 > 0 ? '+' : ''}${hc.growth_12}% in 12 months, to ${hc.total}` : null;
    out.push({ id: c.id, kind: 'rising',
      reason: [grew, filed ? `${filed.label} filed` : null].filter(Boolean).join('; ') || 'Public activity is up sharply',
      ask: filed ? 'Is a round open, and does the fund have pro rata?' : 'Momentum is up. Are they raising, and what do they need from us?' });
  }
  return out.slice(0, limit);
}

const companies = data.companies.map((c) => ({
  id: c.id, name: c.name, domain: c.domain, city: c.city, sector: c.sector,
  bucket: c.bucket, last: c.last_activity, rule: c.rule,
  signals: (c.signals || []).slice(0, 5).map((s) => ({ date: s.date, what: s.what, url: s.url })),
  months: ((c.trend || {}).months || []).map((m) => m.total),
  badges: (c.badges || []).map((b) => b.label),
}));

const out = {
  fund: data.fund, date: data.score_date, total: data.total, counts: data.counts,
  first_month: ((data.companies.find((c) => (c.trend || {}).months) || {}).trend || {}).months?.[0]?.month,
  calls: calls(data.companies), companies,
};
writeFileSync(join(root, 'site', 'census-data.js'), `window.CENSUS = ${JSON.stringify(out)};\n`);
console.log(`site/census-data.js from ${latest}: ${companies.length} companies, ${out.calls.length} calls`);
