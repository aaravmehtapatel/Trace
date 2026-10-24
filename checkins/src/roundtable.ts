// Roundtable over its HTTP tool API (POST /api/tools/<name>, flat JSON body). Same tools the
// SDK wraps, without installing the SDK from a GitHub release tarball. Response field names
// for threads aren't in the docs we read, so the readers below accept the likely shapes and
// `CHECKINS_DEBUG=1` prints raw responses for the first spike.
import { randomUUID } from "node:crypto";
import type { Message } from "./transport.ts";

const WRITE_TOOLS = new Set(["create_thread", "post_thread_message", "write_file", "join_thread", "set_thread_status"]);

export class RoundtableError extends Error {
  constructor(public tool: string, public status: number, message: string) {
    super(`Roundtable ${tool} ${status}: ${message}`);
  }
}

export class RoundtableHttp {
  private base: string;
  constructor(private key: string, url = process.env.ROUNDTABLE_URL ?? "https://roundtable.bot") {
    this.base = url.replace(/\/$/, "");
  }

  async call<T>(tool: string, args: Record<string, unknown>): Promise<T> {
    const body = WRITE_TOOLS.has(tool) ? { request_id: randomUUID(), ...args } : args;
    for (let attempt = 0; ; attempt++) {
      let res: Response;
      try {
        res = await fetch(`${this.base}/api/tools/${tool}`, {
          method: "POST",
          headers: { Authorization: `Bearer ${this.key}`, "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(60_000),
        });
      } catch (e) {
        if (attempt < 4) { await sleep(500 * 2 ** attempt); continue; }
        throw new RoundtableError(tool, 0, String(e));
      }
      const text = await res.text();
      if (process.env.CHECKINS_DEBUG) console.log(`[roundtable] ${tool} ${res.status} ${text.slice(0, 600)}`);
      if (res.ok) return (text ? JSON.parse(text) : {}) as T;
      if ([429, 502, 503, 504].includes(res.status) && attempt < 4) {
        await sleep(Number(res.headers.get("retry-after")) * 1000 || 500 * 2 ** attempt);
        continue;
      }
      throw new RoundtableError(tool, res.status, text.slice(0, 400));
    }
  }

  async createThread(title: string, crossAccount: boolean) {
    const r = await this.call<Record<string, unknown>>("create_thread", { title: title.slice(0, 120), cross_account: crossAccount });
    const thread = (r.thread ?? r) as Record<string, unknown>;
    return { threadId: String(thread.thread_id ?? thread.id ?? r.thread_id), connectUrl: (r.connect_url ?? thread.connect_url) as string | undefined, raw: r };
  }

  async listThreads(): Promise<Record<string, unknown>[]> {
    const r = await this.call<unknown>("list_threads", {});
    return (Array.isArray(r) ? r : ((r as Record<string, unknown>).threads as Record<string, unknown>[]) ?? []);
  }

  async writeFile(path: string, content: string, message: string, bank?: string) {
    return this.call<Record<string, unknown>>("write_file", { path, content, message, ...(bank ? { bank } : {}) });
  }

  async readFile(path: string, bank?: string) {
    return this.call<Record<string, unknown>>("read_file", { path, ...(bank ? { bank } : {}) });
  }

  async listFiles(prefix?: string, bank?: string) {
    const r = await this.call<unknown>("list_files", { ...(prefix ? { prefix } : {}), ...(bank ? { bank } : {}) });
    return (Array.isArray(r) ? r : ((r as Record<string, unknown>).files as { path: string }[]) ?? []) as { path: string; version?: string }[];
  }
}

export function threadMessages(r: unknown): Message[] {
  const list = (Array.isArray(r) ? r : ((r as Record<string, unknown>).messages as unknown[]) ?? []) as Record<string, unknown>[];
  return list.map((m) => ({ id: String(m.id ?? m.message_id), author: String(m.author ?? m.author_name ?? ""), text: String(m.text ?? m.body ?? "") }));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
