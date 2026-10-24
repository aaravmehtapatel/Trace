// One JSON-returning model call, traced to PRISM. Nicrron (the event's model gateway, OpenAI
// format) when NICRRON_API_KEY is set, else the Anthropic API directly. Answers are cached by
// prompt hash in .checkins/cache so a demo re-run is instant and identical; CHECKINS_CACHE=off
// forces fresh calls (do that for PRISM runs).
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";
import type { z } from "zod";
import { type AgentName, traceLlm } from "./tracing.ts";

// CHECKINS_MODEL overrides the model for keys that can't reach the default.
const CLAUDE = process.env.CHECKINS_MODEL ?? "claude-opus-5-5";
const CACHE_DIR = join(process.cwd(), ".checkins", "cache");

export type JsonCall<T> = {
  agent: AgentName;
  session: string;
  step: string;
  system: string;
  user: string;
  schema: z.ZodType<T>;
};

// Tests swap this for a scripted model.
export type Model = (system: string, user: string) => Promise<{ text: string; model: string; tokensIn?: number; tokensOut?: number }>;

let override: Model | undefined;
export function useModel(m: Model | undefined) {
  override = m;
}

function provider(): Model {
  if (override) return override;
  if (process.env.NICRRON_API_KEY) {
    const client = new OpenAI({ baseURL: process.env.NICRRON_BASE_URL ?? "https://api.nicrron.ai/v1", apiKey: process.env.NICRRON_API_KEY });
    const model = process.env.NICRRON_MODEL ?? `anthropic/${CLAUDE}`;
    return async (system, user) => {
      const r = await client.chat.completions.create({
        model,
        max_tokens: 16000,
        response_format: { type: "json_object" },
        messages: [{ role: "system", content: system }, { role: "user", content: user }],
      });
      return { text: r.choices[0]?.message?.content ?? "", model: r.model ?? model,
        tokensIn: r.usage?.prompt_tokens, tokensOut: r.usage?.completion_tokens };
    };
  }
  const censusKey = process.env.CENSUS_ANTHROPIC_API_KEY;
  if (process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN || censusKey) {
    // The census's key (root .env) talks to the public API directly, as in src/ingest/domains.py,
    // so a shell's own ANTHROPIC_* settings for other tools don't interfere.
    // The census key wins when set: a shell's own ANTHROPIC_API_KEY / BASE_URL (set for other
    // tools) pointed check-ins at an endpoint that doesn't serve the model.
    const client = censusKey
      ? new Anthropic({ apiKey: censusKey, authToken: null, baseURL: "https://api.anthropic.com" })
      : new Anthropic();
    return async (system, user) => {
      const r = await client.messages.create({ model: CLAUDE, max_tokens: 16000, system, messages: [{ role: "user", content: user }] });
      if (r.stop_reason === "refusal") throw new Error(`model declined (${r.stop_details?.category ?? "no category"})`);
      const text = r.content.map((b) => (b.type === "text" ? b.text : "")).join("");
      return { text, model: r.model, tokensIn: r.usage.input_tokens, tokensOut: r.usage.output_tokens };
    };
  }
  throw new Error("No model key. Set CENSUS_ANTHROPIC_API_KEY in the census's .env, or ANTHROPIC_API_KEY or NICRRON_API_KEY in checkins/.env.");
}

function extractJson(text: string): unknown {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < start) throw new Error("no JSON object in the reply");
  return JSON.parse(text.slice(start, end + 1));
}

export async function callJson<T>(c: JsonCall<T>): Promise<T> {
  const cacheOn = process.env.CHECKINS_CACHE !== "off" && !override;
  // Request ids carry a random suffix ("tallyline-2026-Q3-1a2b3c"); leave it out of the key so a
  // rerun with the same choices hits the cache. The prompt itself is unchanged.
  const stable = (t: string) => t.replace(/(-\d{4}-Q\d)-[0-9a-f]{6}\b/g, "$1-<id>");
  const key = createHash("sha256").update(`${c.step}\n${stable(c.system)}\n${stable(c.user)}`).digest("hex").slice(0, 24);
  const cached = join(CACHE_DIR, `${key}.json`);
  if (cacheOn && existsSync(cached)) return c.schema.parse(JSON.parse(readFileSync(cached, "utf8")));

  const model = provider();
  let user = c.user;
  for (let attempt = 1; ; attempt++) {
    const startedAt = Date.now();
    const input = [{ role: "system", content: c.system }, { role: "user", content: user }];
    let reply;
    try {
      reply = await model(c.system, user);
    } catch (e) {
      traceLlm({ agent: c.agent, session: c.session, step: c.step, model: CLAUDE, input, output: "", startedAt, error: String(e) });
      throw e;
    }
    traceLlm({ agent: c.agent, session: c.session, step: c.step, model: reply.model, input, output: reply.text, startedAt,
      tokensIn: reply.tokensIn, tokensOut: reply.tokensOut });
    try {
      const value = c.schema.parse(extractJson(reply.text));
      if (cacheOn) {
        mkdirSync(CACHE_DIR, { recursive: true });
        writeFileSync(cached, JSON.stringify(value, null, 2));
      }
      return value;
    } catch (e) {
      if (attempt >= 2) throw new Error(`${c.step}: model reply failed validation twice: ${e}`);
      user = `${c.user}\n\nYour previous reply was not valid: ${String(e).slice(0, 800)}\nReply again with only the JSON object.`;
    }
  }
}
