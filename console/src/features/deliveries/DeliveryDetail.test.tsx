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
  it("首屏提供当前状态、下一动作和可键盘访问的章节导航", () => {
    render(<MemoryRouter><DeliveryDetail delivery={delivery()} events={[]} evidence={[]} decisionPending={false} onDecision={vi.fn()}/></MemoryRouter>);

    expect(screen.getByRole("region", { name: "交付行动摘要" })).toBeTruthy();
    expect(screen.getByText("当前状态")).toBeTruthy();
    expect(screen.getByText("下一步")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "交付详情章节" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "候选与发布" })).toBeTruthy();
  });

  it("长交付目标默认收起，并允许用户显式展开完整正文", async () => {
    const longRequest = delivery();
    longRequest.user_request = "实现跨四个独立仓库的健康检查、可观测性、失败恢复和发布验证，".repeat(8);
    render(<MemoryRouter><DeliveryDetail delivery={longRequest} events={[]} evidence={[]} decisionPending={false} onDecision={vi.fn()}/></MemoryRouter>);

    const title = screen.getByRole("heading", { level: 2 });
    expect(title.className).toContain("delivery-request-title");
    expect(title.className).not.toContain("expanded");
    await userEvent.click(screen.getByRole("button", { name: "展开完整目标" }));
    expect(title.className).toContain("expanded");
    expect(screen.getByRole("button", { name: "收起目标" })).toBeTruthy();
  });

  it("默认只展示证据与事件摘要，详情由用户按需展开", async () => {
    render(
      <MemoryRouter>
        <DeliveryDetail
          delivery={delivery()}
          evidence={[{
            id: "evidence-1",
            project_id: "pj1",
            delivery_id: "delivery-1",
            kind: "verification",
            source_kind: "verification",
            source_id: "verification-source-1",
            producer_identity: "deterministic-test",
            status: "verified",
            content_sha256: hash,
          }]}
          events={[{
            id: "event-1",
            event_type: "delivery.plan.generated",
            aggregate_type: "delivery",
            aggregate_id: "delivery-1",
            aggregate_version: 3,
            occurred_at: "2026-09-20T00:00:00Z",
          }]}
          decisionPending={false}
          onDecision={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.queryByText("verification-source-1")).toBeNull();
    expect(screen.queryByText("delivery.plan.generated")).toBeNull();
    await userEvent.click(screen.getByText("1/1 条证据已验证"));
    expect(screen.getByText(/verification-source-1/)).toBeTruthy();
    await userEvent.click(screen.getByText("1 条已提交事件"));
    expect(screen.getByText("delivery.plan.generated")).toBeTruthy();
  });

  it("候选摘要默认可见，完整 Diff 和机器日志按需展开", async () => {
    const candidateDelivery = delivery();
    candidateDelivery.repository_candidates = [{
      role: "backend",
      workspace_ref: "workspace-backend",
      repository_ref: "backend-repository",
      candidate: {
        base_revision: "b".repeat(40),
        candidate_revision: "c".repeat(40),
        diff_sha256: hash,
        changed_files: ["src/health.py"],
        candidate_ref: "refs/heads/candidate",
        unified_diff: "+secret-diff-line",
        knowledge_citation_ids: [],
      },
      verification: { status: "passed", commands: ["pytest"], exit_code: 0, log_sha256: hash, redacted_log: "passed", acceptance_ids: ["AC-001"] },
      producer_identity: "codex-cli",
    }];
    render(<MemoryRouter><DeliveryDetail delivery={candidateDelivery} events={[]} evidence={[]} decisionPending={false} onDecision={vi.fn()}/></MemoryRouter>);

    expect(screen.getByText("backend-repository")).toBeTruthy();
    expect(screen.queryByText("+secret-diff-line")).toBeNull();
    await userEvent.click(screen.getByText("查看完整 Diff · 1 个文件"));
    expect(screen.getByText("+secret-diff-line")).toBeTruthy();
    expect(screen.queryByText(/pytest/)).toBeNull();
    await userEvent.click(screen.getByText("查看机器验证 · 通过"));
    expect(screen.getByText("pytest")).toBeTruthy();
  });

  it("计划审批前展示每仓验收责任和原始验收正文", async () => {
    const planned = delivery();
    planned.task!.workcell_acceptance = [{ workcell_key: "frontend", acceptance: [{ acceptance_id: "AC-001", responsibility: "展示健康状态和请求失败状态" }] }];
    render(<MemoryRouter><DeliveryDetail delivery={planned} events={[]} evidence={[]} decisionPending={false} onDecision={vi.fn()}/></MemoryRouter>);
    expect(screen.getByText("展示健康状态和请求失败状态")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "审查计划" }));
    await userEvent.click(screen.getByRole("tab", { name: "边界" }));
    expect(screen.getAllByText("展示健康状态和请求失败状态").length).toBeGreaterThan(1);
  });

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

  it("Partial Apply 只引导 Resume forward，不误导用户创建新交付", () => {
    const partialApply: Delivery = {
      ...delivery(),
      status: "needs_attention",
      version: 12,
      error_code: "REMOTE_MAIN_DRIFTED",
      release_bundle_v2_sha256: hash,
      plan_gate: undefined,
    };

    render(<MemoryRouter><DeliveryDetail delivery={partialApply} events={[]} evidence={[]} decisionPending={false} onDecision={vi.fn()}/></MemoryRouter>);

    expect(screen.getByText(/使用下方四仓发布面板的 Resume forward/)).toBeTruthy();
    expect(screen.getByText(/已成功推进的仓库不会回滚/)).toBeTruthy();
    expect(screen.queryByText(/再创建新的交付/)).toBeNull();
    expect(screen.queryByText(/不会污染任何项目仓库的 Main/)).toBeNull();
  });
});
