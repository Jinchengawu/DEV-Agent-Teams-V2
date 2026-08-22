import { describe, expect, it } from "vitest";
import { orderedStepIds } from "./contracts";

describe("published Journey contract", () => {
  it("uses ACWM step order rather than canvas coordinates", () => {
    const revision = {
      journey_id: "backend-delivery",
      revision: 2,
      definition: {
        steps: [
          { kind: "stage", id: "requirements" },
          { kind: "approval_gate", id: "approve-plan" },
          { kind: "stage", id: "delivery" },
        ],
      },
      binding_snapshot: {},
      fingerprint: "a".repeat(64),
      published_at: "2026-08-22T00:00:00Z",
      layout: { delivery: { x: 0 }, requirements: { x: 999 } },
    };

    expect(orderedStepIds(revision)).toEqual([
      "requirements",
      "approve-plan",
      "delivery",
    ]);
  });
});
