// How packets travel between the fund's agent and the startup's agent. Roundtable caps a chat
// post at 8,000 characters and 20 posts an hour, so a packet goes as one readable headline
// plus as few JSON parts as fit, and the reader reassembles them.
import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { Packet } from "./protocol.ts";
import { RoundtableHttp, threadMessages } from "./roundtable.ts";

export type Message = { id: string; author: string; text: string };

export interface Thread {
  readonly label: string;
  post(text: string): Promise<void>;
  // Messages after the last call, oldest first.
  poll(): Promise<Message[]>;
}

const PART = 7_000;
const HEAD = /^CHECKIN\/1 (\S+) (\d+)\/(\d+)\n/;

export async function sendPacket(thread: Thread, packet: Packet, headline: string): Promise<void> {
  const json = JSON.stringify(packet);
  const id = randomUUID().slice(0, 8);
  const total = Math.ceil(json.length / PART);
  await thread.post(headline);
  for (let i = 0; i < total; i++) await thread.post(`CHECKIN/1 ${id} ${i + 1}/${total}\n${json.slice(i * PART, (i + 1) * PART)}`);
}

export class PacketReader {
  private parts = new Map<string, string[]>();
  constructor(private thread: Thread) {}

  async next<T extends Packet["type"]>(type: T, opts: { timeoutMs?: number; pollMs?: number; signal?: AbortSignal } = {}): Promise<Extract<Packet, { type: T }>> {
    const deadline = Date.now() + (opts.timeoutMs ?? 30 * 60_000);
    let wait = opts.pollMs ?? 2_000;
    while (Date.now() < deadline) {
      opts.signal?.throwIfAborted();
      for (const m of await this.thread.poll()) {
        const h = HEAD.exec(m.text);
        if (!h) continue;
        const [, id, n, total] = h;
        const got = this.parts.get(id) ?? Array<string>(Number(total)).fill("");
        got[Number(n) - 1] = m.text.slice(h[0].length);
        this.parts.set(id, got);
        if (got.every((p) => p)) {
          this.parts.delete(id);
          const packet = Packet.parse(JSON.parse(got.join("")));
          if (packet.type === type) return packet as Extract<Packet, { type: T }>;
        }
      }
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 1.5, 15_000); // Roundtable asks bots not to poll in a tight loop
    }
    throw new Error(`timed out waiting for ${type} on ${this.thread.label}`);
  }
}

// Both agents on one machine: a JSONL file per thread.
export class LocalThread implements Thread {
  private seen = 0;
  constructor(private file: string, private author: string) {
    mkdirSync(dirname(file), { recursive: true });
  }
  get label() {
    return this.file;
  }
  async post(text: string) {
    appendFileSync(this.file, JSON.stringify({ id: randomUUID(), author: this.author, text }) + "\n");
  }
  async poll() {
    if (!existsSync(this.file)) return [];
    const all = readFileSync(this.file, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Message);
    const fresh = all.slice(this.seen).filter((m) => m.author !== this.author);
    this.seen = all.length;
    return fresh;
  }
}

// A Roundtable chat thread, possibly cross-workspace. Each side has its own thread id.
export class RoundtableThread implements Thread {
  private after: string | undefined;
  private mine = new Set<string>();
  constructor(private rt: RoundtableHttp, private threadId: string, private opts: { retryMs?: number } = {}) {}
  get label() {
    return `roundtable thread ${this.threadId}`;
  }
  async post(text: string) {
    // A cross-workspace thread rejects posts with 409 until the other side accepts the link.
    for (let wait = this.opts.retryMs ?? 5_000; ; wait = Math.min(wait * 1.5, 30_000)) {
      try {
        const r = await this.rt.call<Record<string, unknown>>("post_thread_message", { thread_id: this.threadId, text });
        const id = (r.message as { id?: string } | undefined)?.id ?? (r.id as string | undefined);
        if (id) this.mine.add(id);
        return;
      } catch (e) {
        if ((e as { status?: number }).status !== 409) throw e;
        console.log("  waiting for the other side to accept the chat link...");
        await new Promise((r) => setTimeout(r, wait));
      }
    }
  }
  async poll() {
    const r = await this.rt.call<unknown>("read_thread", { thread_id: this.threadId, ...(this.after ? { after: this.after } : {}) });
    const msgs = threadMessages(r).filter((m) => !this.mine.has(m.id));
    if (msgs.length) this.after = msgs[msgs.length - 1].id;
    return msgs;
  }
}
