// The startup's source of truth: a folder of markdown and system exports. `people/` never
// leaves this module, whatever the model is told, so the policy holds even if a prompt fails.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

export type Doc = { path: string; version: string; content: string; system: boolean };

const NEVER_SHARED = ["people/"];

// `includeConfidential` is only for copying the whole folder into the company's own bank.
export function loadDataroom(root: string, opts: { includeConfidential?: boolean } = {}): Doc[] {
  const docs: Doc[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir).sort()) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(md|csv)$/.test(name)) {
        const path = relative(root, full).split(sep).join("/");
        if (!opts.includeConfidential && NEVER_SHARED.some((p) => path.startsWith(p))) continue;
        const content = readFileSync(full, "utf8");
        docs.push({ path, version: createHash("sha256").update(content).digest("hex").slice(0, 8), content,
          system: path.startsWith("systems/") });
      }
    }
  };
  walk(root);
  return docs;
}

const squash = (s: string) => s.toLowerCase().replace(/[*_`|>#]/g, " ").replace(/\s+/g, " ").trim();

// A citation counts only if its quote is really in the file it names.
export function quoteFound(doc: Doc | undefined, quote: string): boolean {
  return !!doc && quote.trim().length >= 4 && squash(doc.content).includes(squash(quote));
}
