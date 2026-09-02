// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
    requirements: { summary: "实现健康检查", non_goals: [], risks: [], acceptance_criteria: [{ id: "AC-001", statement: "返回健康状态" }], knowledge_citation_ids: [] },
    task: { title: "实现健康检查", instructions: "修改受限路径", acceptance_ids: ["AC-001"], system_policy: { allowed_paths: ["src/**"], verification_commands: ["python -m unittest"] }, knowledge_citation_ids: [] },
    plan_gate: { gate_id: "gate-1", subject_kind: "plan", artifact_id: "task-1", subject_sha256: hash, revision: 1 },
    repository_candidates: [],
    journey_binding_snapshot: {},
    resolved_journey_sha256: hash,
    evidence_identity: "deterministic-test",
    planning_identity: "codex-simulated-hermes",
  };
}

describe("交付黄金纵切", () => {
  it("把计划审批映射为带明确语义的真实命令", async () => {
    const onDecision = vi.fn();
    render(<MemoryRouter><DeliveryDetail delivery={delivery()} events={[]} evidence={[]} decisionPending={false} onDecision={onDecision}/></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: "批准计划并开始设计" }));
    expect(screen.getByRole("alertdialog", { name: "批准计划并启动 UI 设计" })).toBeTruthy();
    expect(onDecision).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认批准计划" }));
    expect(onDecision).toHaveBeenCalledWith("approve-plan");
    expect(screen.getByText("当前阶段尚无证据。只有真实产物生成后才会出现记录。")).toBeTruthy();
  });

  it("把设计审批保持为独立 Gate，不能绕过后直接发布", async () => {
    const onDecision = vi.fn();
    const designDelivery = delivery();
    designDelivery.status = "awaiting_design_decision";
    designDelivery.version = 4;
    designDelivery.design_gate = { gate_id: "gate-design", subject_kind: "design", artifact_id: "candidate-design", subject_sha256: hash, revision: 1 };
    render(<MemoryRouter><DeliveryDetail delivery={designDelivery} events={[]} evidence={[]} decisionPending={false} onDecision={onDecision}/></MemoryRouter>);

    await userEvent.click(screen.getByRole("button", { name: "批准设计并开始前后端实现" }));
    expect(screen.getByRole("alertdialog", { name: "批准 UI 设计候选" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "确认批准设计" }));
    expect(onDecision).toHaveBeenCalledWith("approve-design");
    expect(screen.queryByRole("button", { name: /四仓发布/ })).toBeNull();
  });

  it("发布失败只提供知识发布重试，不要求重跑 Agent", async () => {
    const retryPublication = vi.fn();
    render(
      <MemoryRouter>
        <DeliveryDetail
          delivery={{ ...delivery(), status: "planning", plan_gate: undefined }}
          events={[]}
          evidence={[]}
          publications={[{
            id: "publication-1",
            artifact_key: "primary",
            contract_id: "requirement-artifact-v1",
            status: "failed",
            attempt_count: 1,
            error_code: "KNOWLEDGE_PUBLICATION_WRITE_FAILED",
            version: 2,
          }]}
          decisionPending={false}
          publicationRetryPending={false}
          onRetryPublication={retryPublication}
          onDecision={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("知识发布阻塞")).toBeTruthy();
    expect(screen.getByText(/AgentRun 与 Stage 已成功/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "只重试发布" }));
    expect(retryPublication).toHaveBeenCalledWith("publication-1", 2);
  });

  it("External V2 发布明确表达 Forward-only 且不承诺回滚", async () => {
    const onDecision = vi.fn();
    const candidateRevision = "b".repeat(40);
    const baseRevision = "c".repeat(40);
    const externalDelivery: Delivery = {
      ...delivery(),
      status: "awaiting_candidate_decision",
      version: 8,
      plan_gate: undefined,
      release_bundle_v2_sha256: hash,
      workcell_candidates: {
        design: {
          candidate_id: "candidate-design",
          workcell_key: "design",
          workspace_binding_id: "workspace-design",
          base_revision: baseRevision,
          candidate_revision: candidateRevision,
          diff_sha256: hash,
          verification_sha256: hash,
          review_artifact_ids: ["review-design"],
          evidence_sha256: hash,
        },
      },
      candidate_gate: {
        gate_id: "approve-release",
        subject_kind: "release-bundle",
        artifact_id: "bundle-v2",
        subject_sha256: hash,
        revision: 7,
      },
    };
    render(<MemoryRouter><DeliveryDetail delivery={externalDelivery} events={[]} evidence={[]} decisionPending={false} onDecision={onDecision}/></MemoryRouter>);

    expect(screen.getByText("External ReleaseBundleV2 已通过系统校验")).toBeTruthy();
    expect(screen.getAllByText("Candidate bbbbbbbbbbbb").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "批准四仓 Forward-only 发布" }));
    expect(screen.getByRole("alertdialog", { name: "批准四仓 Forward-only ReleaseBundleV2" })).toBeTruthy();
    expect(screen.getByText(/已成功仓库不回滚/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "确认 Forward-only 发布" }));
    expect(onDecision).toHaveBeenCalledWith("accept-candidate");
  });
});
