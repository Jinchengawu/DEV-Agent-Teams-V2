// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Delivery } from "../../shared/api/client";
import { DeliveryDetail } from "./DeliveryDetail";

const hash = "a".repeat(64);

function delivery(): Delivery {
  return {
    id: "delivery-1",
    project_id: "pj1",
    workspace_id: "backend-demo",
    user_request: "增加健康检查接口",
    status: "awaiting_plan_decision",
    version: 3,
    requirements: { summary: "实现健康检查", non_goals: [], risks: [], acceptance_criteria: [{ id: "AC-001", statement: "返回健康状态" }] },
    task: { title: "实现健康检查", instructions: "修改受限路径", acceptance_ids: ["AC-001"], system_policy: { allowed_paths: ["src/**"], verification_commands: ["python -m unittest"] } },
    plan_gate: { gate_id: "gate-1", subject_kind: "plan", artifact_id: "task-1", subject_sha256: hash, revision: 1 },
    journey_binding_snapshot: {},
    resolved_journey_sha256: hash,
    evidence_identity: "deterministic-test",
    planning_identity: "codex-simulated-hermes",
  };
}

describe("交付黄金纵切", () => {
  it("把计划审批映射为带明确语义的真实命令", async () => {
    const onDecision = vi.fn();
    render(<DeliveryDetail delivery={delivery()} events={[]} evidence={[]} decisionPending={false} onDecision={onDecision}/>);
    await userEvent.click(screen.getByRole("button", { name: "批准计划并开始执行" }));
    expect(onDecision).toHaveBeenCalledWith("approve-plan");
    expect(screen.getByText("当前阶段尚无证据。只有真实产物生成后才会出现记录。")).toBeTruthy();
  });
});
