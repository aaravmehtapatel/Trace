// The startup's agent: answers a fund's questions from the company's own data room, citing the
// file and line behind each answer. The model drafts; code decides what counts as supported:
// a citation must quote its file verbatim, and the tier comes from what kind of file it is.
import { z } from "zod";
import { type Doc, quoteFound } from "./dataroom.ts";
import { callJson } from "./llm.ts";
import type { Answer, Attachment, CheckinRequest, CheckinResponse } from "./protocol.ts";

const Draft = z.object({
  answers: z.array(z.object({
    question_id: z.string(),
    answer: z.string(),
    sources: z.array(z.object({ path: z.string(), quote: z.string() })),
  })),
});

export const NOT_COVERED = "The data room doesn't cover this.";

const SYSTEM = `You are the check-in agent for a startup. An investor's agent has sent questions. You answer only from the company's data room, which is given to you in full.

Rules:
- Every factual claim must be backed by a source: the file path and a short verbatim quote (under 200 characters) copied exactly from that file. Quotes are checked by code; a quote that isn't in the file is thrown away.
- For numbers, prefer the system exports under systems/ (billing, bank). Report the exact figures. If an export and a document disagree, say so and cite both.
- If nothing in the data room answers a question, set "answer" to exactly "${NOT_COVERED}" and "sources" to []. Never estimate, infer a number that isn't written down, or fill a gap from general knowledge.
- Follow agent-policy.md. Answer what was asked; don't volunteer unrelated material.
- Write plainly and briefly, the way a careful CFO would. No spin.

Reply with only a JSON object: {"answers":[{"question_id":"...","answer":"...","sources":[{"path":"...","quote":"..."}]}]} with one entry per question, in order.`;

export async function draftResponse(req: CheckinRequest, docs: Doc[], session: string): Promise<CheckinResponse> {
  const room = docs.map((d) => `===== ${d.path} (version ${d.version}) =====\n${d.content.trim()}`).join("\n\n");
  const questions = req.questions.map((q) => `${q.id}. ${q.text}`).join("\n");
  const draft = await callJson({
    agent: "startup-agent", session, step: `answer-round-${req.round}`, system: SYSTEM, schema: Draft,
    user: `Requested by: ${req.fund} for the ${req.period} check-in (round ${req.round}).\n\nQuestions:\n${questions}\n\nData room:\n\n${room}`,
  });
  return ground(req, draft.answers, docs);
}

export function ground(req: CheckinRequest, drafted: z.infer<typeof Draft>["answers"], docs: Doc[]): CheckinResponse {
  const byPath = new Map(docs.map((d) => [d.path, d]));
  const answers: Answer[] = req.questions.map((q) => {
    const a = drafted.find((d) => d.question_id === q.id);
    const sources = (a?.sources ?? [])
      .filter((s) => quoteFound(byPath.get(s.path), s.quote))
      .map((s) => ({ path: s.path, version: byPath.get(s.path)!.version, quote: s.quote.trim() }));
    if (!a || !sources.length) return { question_id: q.id, answer: NOT_COVERED, tier: "unanswered", sources: [], founder_edited: false };
    const tier = sources.some((s) => byPath.get(s.path)!.system) ? "verified" : "documented";
    return { question_id: q.id, answer: a.answer.trim(), tier, sources, founder_edited: false };
  });
  return { type: "checkin.response", request_id: req.id, round: req.round, company: req.company, answers,
    attachments: attachmentsFor(answers, byPath), approved_by: "", approved_at: "" };
}

// The raw exports behind verified answers travel with the response, so the fund can recompute.
export function attachmentsFor(answers: Answer[], byPath: Map<string, Doc>): Attachment[] {
  const paths = new Set(answers.flatMap((a) => a.sources.map((s) => s.path)).filter((p) => byPath.get(p)?.system));
  return [...paths].sort().map((p) => ({ path: p, version: byPath.get(p)!.version, content: byPath.get(p)!.content }));
}
