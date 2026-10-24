// PRISM (Block Convey) tracing over its HTTP ingest. There is no npm SDK, and the HTTP path
// is the one that sets agent_id/agent_name explicitly, which PRISM uses to tell versions apart.
// Off without PRISMTRACE_API_KEY. Never throws and never blocks the agents.
import { randomUUID } from "node:crypto";

export const AGENT_VERSION = process.env.CHECKINS_AGENT_VERSION ?? "v1";

export type AgentName = "startup-agent" | "vc-agent";

const LABELS: Record<AgentName, string> = { "startup-agent": "Startup check-in agent", "vc-agent": "VC check-in agent" };

export function newSession(kind: string): string {
  return `checkins-${kind}-${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}-${randomUUID().slice(0, 6)}`;
}

export type LlmTrace = {
  agent: AgentName;
  session: string;
  step: string;
  model: string;
  input: { role: string; content: string }[];
  output: string;
  startedAt: number;
  tokensIn?: number;
  tokensOut?: number;
  error?: string;
};

export function traceLlm(t: LlmTrace): void {
  const key = process.env.PRISMTRACE_API_KEY;
  const project = process.env.PRISMTRACE_PROJECT_ID;
  if (!key || !project) return;
  const host = process.env.PRISMTRACE_HOST ?? "https://prism.blockconvey.com";
  const body = {
    trace_id: randomUUID(),
    project_id: project,
    session_id: t.session,
    agent_id: `checkins-${t.agent}-${AGENT_VERSION}`,
    agent_name: `${LABELS[t.agent]} ${AGENT_VERSION}`,
    model: t.model,
    input_messages: t.input,
    output_message: t.output,
    latency_ms: Date.now() - t.startedAt,
    token_count_input: t.tokensIn ?? 0,
    token_count_output: t.tokensOut ?? 0,
    metadata: { step: t.step, ...(t.error ? { status: "error", error: t.error.slice(0, 500) } : {}) },
  };
  // Fire and forget, as PRISM's docs recommend: a slow trace must not slow a check-in.
  fetch(`${host}/api/traces`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-PRISMtrace-Key": key },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(5_000),
  }).catch(() => {});
}
