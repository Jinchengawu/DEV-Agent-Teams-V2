import { describe, expect, it } from "vitest";
import { deliveryDecisionPath, pipelineRunReady } from "./api";

describe("pipelineRunReady", () => {
  it("waits until knowledge context preparation has created the PipelineRun", () => {
    expect(pipelineRunReady("run-1", "queued")).toBe(false);
    expect(pipelineRunReady("run-1", "preparing_context")).toBe(false);
    expect(pipelineRunReady("run-1", "planning")).toBe(true);
  });

  it("requires a frozen PipelineRun id", () => {
    expect(pipelineRunReady(null, "planning")).toBe(false);
  });
});

describe("deliveryDecisionPath", () => {
  it("uses the explicit release decision endpoint for ReleaseBundleV2", () => {
    expect(deliveryDecisionPath(
      { release_bundle_v2_sha256: "a".repeat(64) },
      "accept-candidate",
    )).toBe("release-decision");
  });

  it("keeps the legacy candidate endpoint for managed and single-repository deliveries", () => {
    expect(deliveryDecisionPath(
      { release_bundle_v2_sha256: null },
      "accept-candidate",
    )).toBe("candidate-decision");
  });
});
