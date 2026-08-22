import { z } from "zod";

export const journeyRevisionSchema = z.object({
  journey_id: z.string().min(1),
  revision: z.number().int().positive(),
  definition: z.object({
    steps: z.array(z.object({
      kind: z.enum(["stage", "approval_gate"]),
      id: z.string().min(1),
    }).passthrough()),
  }).passthrough(),
  binding_snapshot: z.record(z.string(), z.record(z.string(), z.unknown())),
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  published_at: z.string(),
});

export function parseJourneyRevisions(payload: unknown) {
  return z.array(journeyRevisionSchema).parse(payload);
}

export function orderedStepIds(payload: unknown): string[] {
  return journeyRevisionSchema.parse(payload).definition.steps.map((step) => step.id);
}
