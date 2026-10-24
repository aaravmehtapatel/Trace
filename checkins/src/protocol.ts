// The two messages the agents exchange. Both sides validate what they receive, so a
// malformed or tampered packet fails loudly instead of reaching a report.
import { z } from "zod";

export const Question = z.object({
  id: z.string(),
  text: z.string(),
  kind: z.enum(["standard", "tailored", "followup"]),
  why: z.string().optional(),
});
export type Question = z.infer<typeof Question>;

export const CheckinRequest = z.object({
  type: z.literal("checkin.request"),
  id: z.string(),
  round: z.number().int().min(1),
  fund: z.string(),
  company: z.string(),
  period: z.string(),
  questions: z.array(Question).min(1),
});
export type CheckinRequest = z.infer<typeof CheckinRequest>;

// verified: backed by a system-of-record export (billing, bank).
// documented: backed by a document the team wrote.
// unanswered: nothing in the data room supports an answer.
// withheld: the founder redacted it before sending.
export const Tier = z.enum(["verified", "documented", "unanswered", "withheld"]);
export type Tier = z.infer<typeof Tier>;

export const Source = z.object({
  path: z.string(),
  version: z.string(),
  quote: z.string(),
});
export type Source = z.infer<typeof Source>;

export const Attachment = z.object({ path: z.string(), version: z.string(), content: z.string() });
export type Attachment = z.infer<typeof Attachment>;

export const Answer = z.object({
  question_id: z.string(),
  answer: z.string(),
  tier: Tier,
  sources: z.array(Source),
  founder_edited: z.boolean().default(false),
});
export type Answer = z.infer<typeof Answer>;

export const CheckinResponse = z.object({
  type: z.literal("checkin.response"),
  request_id: z.string(),
  round: z.number().int().min(1),
  company: z.string(),
  answers: z.array(Answer),
  // Raw system-of-record exports cited by verified answers, so the fund can recompute.
  attachments: z.array(Attachment),
  approved_by: z.string(),
  approved_at: z.string(),
});
export type CheckinResponse = z.infer<typeof CheckinResponse>;

export type Packet = CheckinRequest | CheckinResponse;
export const Packet = z.discriminatedUnion("type", [CheckinRequest, CheckinResponse]);
