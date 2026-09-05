// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkcellExecutionPanel } from "./WorkcellExecutionPanel";

const hash = "a".repeat(64);
const revision = "b".repeat(40);
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("Workcell 运行可观测性", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("显示无效 Review 的错误原因与保留证据，不能显示为零问题通过", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo) => {
      if (String(input).includes("/artifacts/")) return response({ reference: { sha256: hash, media_type: "application/json" }, content: JSON.stringify({ summary: "<img src=x onerror=alert(1)>", blocking_findings: [] }) });
      if (String(input).endsWith("/workcell-runs")) return response([{
        workcell_run: { id: "invalid-review", delivery_id: "delivery-1", stage_path: "frontend", workcell_key: "frontend", status: "failed", loop_iteration: 1, version: 4, workcell_snapshot_sha256: hash, error_code: "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE", workcell_snapshot: { workspace: { repository_uri: "project/frontend" }, method_snapshot_sha256: hash } },
        agent_runs: [{ id: "reviewer", run_role: "child", delegate_purpose: "review", status: "failed", slot_key: "delegate_2", workspace_access: "candidate_read", depth: 1, resolved_binding_hash: hash, artifact_envelopes: [{ contract_id: "review-artifact-v1", reference: { sha256: hash } }] }],
        attempts: [{ id: "review-attempt", agent_run_id: "reviewer", phase: "delegate", ordinal: 1, status: "failed", error_code: "WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE", result_artifact_sha256: hash }], reviews: [],
      }]);
      return response({ candidates: [], pull_requests: [], remote_apply_receipts: [] });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkcellExecutionPanel deliveryId="delivery-1" projectId="project-1"/></QueryClientProvider>);
    expect(await screen.findByText("Review 输出无效，当前轮次未通过")).toBeTruthy();
    expect(screen.getAllByText("WORKCELL_REVIEW_FINDING_OUT_OF_SCOPE").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /查看 Review 原始输出/ }));
    expect(await screen.findByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
  });

  it("同时显示 Main/Child/Attempt、Method Hash、PR 和 Remote Apply Receipt", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo) => {
      const url = String(input);
      if (url.endsWith("/workcell-runs")) return response([{
        workcell_run: { id: "run-1", delivery_id: "delivery-1", pipeline_run_id: "pipeline-run-1", stage_attempt_id: "stage-1", stage_path: "frontend-repair/frontend", loop_iteration: 1, workcell_key: "frontend", workcell_snapshot: { team_template_revision_id: "software-delivery-team:1", team_template_sha256: hash, pipeline_revision_id: "agent-workcell-delivery:1", pipeline_revision_sha256: hash, stage_path: "frontend-repair/frontend", workcell_key: "frontend", workspace: { workspace_binding_id: "workspace-frontend", kind: "git_repository_v1", adapter_type: "external-git", repository_uri: "https://github.com/example/frontend.git", base_revision: "c".repeat(40), verification_sha256: hash }, delegation_policy: { max_children: 3, max_concurrency: 2, max_writers: 1, max_depth: 1, wall_clock_budget_seconds: 900 }, slot_bindings: [], slot_method_bindings: { delegate_1: "bmad-build" }, slot_purpose_bindings: { delegate_1: "workspace_write" }, method_snapshot_sha256: hash, input_artifacts: [] }, workcell_snapshot_sha256: hash, status: "succeeded", main_agent_run_id: "main-1", version: 5, deadline_at: new Date().toISOString() },
        agent_runs: [{ id: "main-1", delivery_id: "delivery-1", pipeline_revision_id: "agent-workcell-delivery:1", binding_site: "frontend:main", resolved_binding_hash: hash, deployment_snapshot: {}, attempt_id: "attempt-main", runtime_identity: "codex-cli", status: "succeeded", workcell_run_id: "run-1", root_agent_run_id: "main-1", depth: 0, run_role: "main", workspace_access: "none", slot_key: "main", artifact_envelopes: [] }, { id: "child-1", delivery_id: "delivery-1", pipeline_revision_id: "agent-workcell-delivery:1", binding_site: "frontend:delegate_1", resolved_binding_hash: hash, deployment_snapshot: {}, attempt_id: "attempt-child", runtime_identity: "codex-cli", status: "succeeded", workcell_run_id: "run-1", parent_agent_run_id: "main-1", root_agent_run_id: "main-1", depth: 1, run_role: "child", delegate_purpose: "workspace_write", workspace_access: "workspace_write", slot_key: "delegate_1", artifact_envelopes: [] }],
        attempts: [{ id: "attempt-main", agent_run_id: "main-1", phase: "planning", ordinal: 1, provider_binding_hash: hash, runtime_identity: "codex-cli", status: "succeeded", result_artifact_sha256: hash }, { id: "attempt-child", agent_run_id: "child-1", phase: "delegate", ordinal: 1, provider_binding_hash: hash, runtime_identity: "codex-cli", status: "succeeded", result_artifact_sha256: hash }],
        reviews: [], verification: { status: "passed" }, result: { candidate_sha: revision, output_artifact_references: [] },
      }]);
      if (url.endsWith("/release-health")) return response({ project_id: "project-1", status: "healthy", delivery_id: "delivery-1", bundle_sha256: hash, version: 1 });
      if (url === "/v1/releases/delivery-1") return response({ delivery_id: "delivery-1", project_id: "project-1", candidates: [{ id: "candidate-1", delivery_id: "delivery-1", project_id: "project-1", workcell_key: "frontend", workspace_binding_id: "workspace-frontend", repository_uri: "https://github.com/example/frontend.git", adapter_type: "external-git", base_revision: "c".repeat(40), candidate_revision: revision, diff_sha256: hash, candidate_branch: "agent-team-os/delivery-1/frontend", verification_sha256: hash, review_artifact_ids: ["review-1"], evidence_sha256: hash, status: "verified" }], pull_requests: [{ candidate_id: "candidate-1", provider: "github", pull_request_id: 7, url: "https://github.com/example/frontend/pull/7", base_branch: "main", head_branch: "agent-team-os/delivery-1/frontend", head_candidate_sha: revision, state: "open", receipt_sha256: hash }], bundle: { delivery_id: "delivery-1", project_id: "project-1", pipeline_revision_id: "agent-workcell-delivery:1", release_contract_snapshot: ["frontend"], candidates: [], bundle_sha256: hash, status: "verified" }, apply_attempt: { delivery_id: "delivery-1", project_id: "project-1", bundle_sha256: hash, status: "completed", version: 2 }, remote_apply_receipts: [{ delivery_id: "delivery-1", ordinal: 0, candidate_id: "candidate-1", workcell_key: "frontend", repository_uri: "https://github.com/example/frontend.git", before_revision: "c".repeat(40), candidate_revision: revision, after_revision: revision, recovered: false, receipt_sha256: hash }], manifest: null });
      return response({});
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkcellExecutionPanel deliveryId="delivery-1" projectId="project-1"/></QueryClientProvider>);

    expect(await screen.findByText("Main · planning + synthesis")).toBeTruthy();
    expect(screen.getByText("Child · workspace_write")).toBeTruthy();
    expect(screen.getByText(/\$bmad-build/)).toBeTruthy();
    expect(screen.getByText("历史快照未冻结验证方案")).toBeTruthy();
    expect(await screen.findByText("#7 · open")).toBeTruthy();
    expect(screen.getByText("已推进 main")).toBeTruthy();
  });
});
