// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ExternalKnowledgePanel } from "./ExternalKnowledgePanel";

const now = "2026-09-02T00:00:00Z";
const hash = "a".repeat(64);
const project = {
  project: { id: "project-1", slug: "project-1", name: "项目一", description: "", lifecycle_status: "active", version: 1, created_by: "admin", created_at: now, updated_at: now },
  workspace: { project_id: "project-1", workspace_id: "project:project-1", repository_ref: "projects/project-1", status: "ready", provision_attempt: 1, created_at: now, updated_at: now },
  pipeline_bindings: [], deployment_access: [], knowledge_sources: [], repositories: [], active_delivery_id: null,
  knowledge_source_approvals: [{ id: "approval-1", project_id: "project-1", binding_id: "binding-1", enabled: true, rag_enabled: true, version: 1, created_by: "admin", created_at: now, updated_at: now }],
};

afterEach(() => vi.unstubAllGlobals());

test("项目知识页可以同步已批准飞书来源并运行服务端编译范围的检索预览", async () => {
  const calls: Array<{ url: string; method: string }> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url === "/v1/features") return response({ feishu_tenant_sync_v1: true, knowledge_hybrid_index_v1: true, delivery_knowledge_context_v1: true });
    if (url === "/v1/projects/project-1") return response(project);
    if (url === "/v1/projects/project-1/knowledge-bindings/binding-1/nodes") return response([{ external_id: "node-1", external_space_id: "space-1", parent_external_id: null, source_id: "docx:architecture", title: "架构规范", kind: "document", provider_revision: "rev-1", updated_at: now }]);
    if (url === "/v1/projects/project-1/knowledge-sync-jobs?binding_id=binding-1") return response([]);
    if (url === "/v1/projects/project-1/knowledge-snapshots?binding_id=binding-1") return response([{ id: "snapshot-1", binding_id: "binding-1", source_id: "docx:architecture", provider_revision: "rev-1", content_type: "text/plain", artifact: { uri: "artifact://sha256/" + hash, sha256: hash, media_type: "application/json", size_bytes: 120 }, normalized_text_sha256: hash, source_url: "https://example.invalid/wiki/architecture", fetched_by_product_user_id: "admin", fetched_at: now }]);
    if (url === "/v1/projects/project-1/knowledge-retrieval-options?provider_binding_id=binding-1") return response([{ provider_binding_id: "binding-1", index_revision_id: "index-1", index_profile_revision_id: "profile-1", retrieval_policy_revision_id: "policy-1" }]);
    if (url === "/v1/projects/project-1/knowledge-sync-jobs" && method === "POST") return response({ id: "job-1", project_id: "project-1", binding_id: "binding-1", source_id: "docx:architecture", idempotency_key: "key", status: "succeeded", attempt: 1, max_attempts: 3, requested_by: "admin", version: 2, created_at: now, updated_at: now }, 202);
    if (url === "/v1/projects/project-1/knowledge-retrieval-preview" && method === "POST") return response({ hits: [{ citation_id: "CIT-1", chunk_id: "chunk-1", source_id: "docx:architecture", source_url: "https://example.invalid/wiki/architecture", content: "四仓隔离", score: { lexical_rank: 1, lexical_score: 1, vector_rank: 1, vector_distance: 0, rrf_score: 1 } }], receipt: { id: "receipt-1", project_id: "project-1", provider_binding_id: "binding-1", index_revision_id: "index-1", retrieval_policy_revision_id: "policy-1", query_sha256: hash, allowed_source_set_sha256: hash, citation_ids: ["CIT-1"], requested_by: "admin", created_at: now } });
    return response({}, 404);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><ExternalKnowledgePanel projectId="project-1"/></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "外部知识快照与 RAG" })).toBeTruthy();
  expect((await screen.findAllByText("架构规范")).length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole("button", { name: "同步当前来源" }));
  await userEvent.type(screen.getByRole("textbox", { name: "检索查询" }), "workspace 隔离");
  await userEvent.click(screen.getByRole("button", { name: "运行 RAG 预览" }));

  expect(await screen.findByText("四仓隔离")).toBeTruthy();
  await waitFor(() => expect(calls).toContainEqual({ url: "/v1/projects/project-1/knowledge-sync-jobs", method: "POST" }));
  expect(calls).toContainEqual({ url: "/v1/projects/project-1/knowledge-retrieval-preview", method: "POST" });
});

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
