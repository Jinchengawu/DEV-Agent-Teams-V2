// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { IdentityProvider } from "../identity/AuthGate";
import { ProjectKnowledgeGovernance } from "./ProjectKnowledgeGovernance";
import type { ProjectDetail } from "./api";

const now = "2026-09-02T00:00:00Z";
const user = { id: "admin", username: "admin", display_name: "系统管理员", role: "administrator", enabled: true, authorization_version: 1, version: 1, created_at: now, updated_at: now } as const;
const detail = {
  project: { id: "project-1", slug: "project-1", name: "项目一", description: "", lifecycle_status: "active", version: 1, created_by: "admin", created_at: now, updated_at: now },
  workspace: { project_id: "project-1", workspace_id: "project:project-1", repository_ref: "projects/project-1", status: "ready", provision_attempt: 1, created_at: now, updated_at: now },
  pipeline_bindings: [], deployment_access: [], knowledge_sources: [], knowledge_source_approvals: [], repositories: [], active_delivery_id: null,
} satisfies ProjectDetail;
const binding = { id: "binding-1", connection_id: "connection-1", display_name: "研发 Wiki", external_space_id: "space-1", root_node_token: null, status: "ready", authorization_version: 1, version: 1, replaces_binding_id: null, created_by: "admin", created_at: now, updated_at: now, last_permission_probe_at: now, last_error_code: null };

afterEach(() => vi.unstubAllGlobals());

test("管理员只能从 Tenant Binding 目录批准项目知识范围", async () => {
  const calls: Array<{ url: string; method: string; body?: string }> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (url === "/v1/features") return response({ feishu_tenant_sync_v1: true, knowledge_hybrid_index_v1: true, delivery_knowledge_context_v1: true });
    if (url === "/v1/projects/project-1/memberships") return response([{ project_id: "project-1", user_id: "admin", role: "owner", version: 1 }]);
    if (url === "/v1/users") return response([user]);
    if (url === "/v1/knowledge/provider-bindings-v2") return response([binding]);
    if (url === "/v1/projects/project-1/knowledge-source-approvals/binding-1" && method === "PUT") return response({ id: "approval-1", project_id: "project-1", binding_id: "binding-1", enabled: true, rag_enabled: true, version: 1, created_by: "admin", created_at: now, updated_at: now });
    return response({}, 404);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><IdentityProvider user={user}><ProjectKnowledgeGovernance detail={detail}/></IdentityProvider></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "成员与知识来源授权" })).toBeTruthy();
  expect(screen.getByText("研发 Wiki")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "批准来源" }));

  await waitFor(() => expect(calls).toContainEqual({
    url: "/v1/projects/project-1/knowledge-source-approvals/binding-1",
    method: "PUT",
    body: JSON.stringify({ enabled: true, rag_enabled: true }),
  }));
});

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
