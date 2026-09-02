import { describe, expect, it } from "vitest";
import { pipelineRunReady } from "./api";

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
