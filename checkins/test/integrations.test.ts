// Request shapes for PRISM and Roundtable, checked against a stub fetch. These don't prove the
// live services accept them (that needs keys); they pin what the docs say we must send.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { RoundtableHttp } from "../src/roundtable.ts";
import { traceLlm } from "../src/tracing.ts";
import { RoundtableThread } from "../src/transport.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  delete process.env.PRISMTRACE_API_KEY;
  delete process.env.PRISMTRACE_PROJECT_ID;
});

type Call = { url: string; headers: Record<string, string>; body: Record<string, unknown> };
function stub(respond: (c: Call) => { status: number; body: unknown }) {
  const calls: Call[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    const c = { url, headers: init.headers as Record<string, string>, body: JSON.parse(String(init.body)) };
    calls.push(c);
    const r = respond(c);
    return new Response(JSON.stringify(r.body), { status: r.status });
  }) as typeof fetch;
  return calls;
}

test("PRISM: off without a key, then one trace per call with a versioned agent name", async () => {
  const calls = stub(() => ({ status: 200, body: {} }));
  const t = { agent: "vc-agent" as const, session: "s1", step: "report", model: "m", input: [{ role: "user", content: "hi" }], output: "ok", startedAt: Date.now() };
  traceLlm(t);
  assert.equal(calls.length, 0);
  process.env.PRISMTRACE_API_KEY = "pt-sk-test";
  process.env.PRISMTRACE_PROJECT_ID = "proj";
  traceLlm(t);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://prism.blockconvey.com/api/traces");
  assert.equal(calls[0].headers["X-PRISMtrace-Key"], "pt-sk-test");
  for (const k of ["project_id", "model", "input_messages", "output_message", "latency_ms"]) assert.ok(k in calls[0].body, k);
  assert.equal(calls[0].body.agent_id, "checkins-vc-agent-v1");
  assert.equal(calls[0].body.session_id, "s1");
});

test("Roundtable: tool calls, request ids on writes, and waiting out a pending chat", async () => {
  let pending = 2;
  const calls = stub((c) => {
    if (c.url.endsWith("/post_thread_message")) return pending-- > 0 ? { status: 409, body: { error: "pending" } } : { status: 200, body: { message: { id: "m-mine" } } };
    if (c.url.endsWith("/read_thread")) return { status: 200, body: { messages: [{ id: "m-mine", author: "me", text: "x" }, { id: "m2", author: "external", text: "hello" }] } };
    return { status: 404, body: {} };
  });
  const rt = new RoundtableHttp("key", "https://rt.test");
  const thread = new RoundtableThread(rt, "th1", { retryMs: 1 });
  await thread.post("hi");
  const posts = calls.filter((c) => c.url === "https://rt.test/api/tools/post_thread_message");
  assert.equal(posts.length, 3, "retried through two 409s");
  assert.equal(posts[0].headers.Authorization, "Bearer key");
  assert.ok(typeof posts[0].body.request_id === "string" && posts[0].body.request_id !== posts[1].body.request_id);
  assert.equal(posts[0].body.thread_id, "th1");

  const got = await thread.poll();
  assert.deepEqual(got.map((m) => m.text), ["hello"], "our own post is filtered out");
  await thread.poll();
  assert.equal(calls[calls.length - 1].body.after, "m2", "the next read starts after the last message");
  assert.ok(!("request_id" in calls[calls.length - 1].body), "reads carry no request id");
});
