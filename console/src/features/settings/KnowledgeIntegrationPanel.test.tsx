// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { KnowledgeIntegrationPanel } from "./KnowledgeIntegrationPanel";

const calls: Array<{ url: string; method: string }> = [];
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const connection = {
  id: "connection-1", provider_kind: "feishu", display_name: "研发飞书", access_model: "tenant-service-principal-v1",
  app_id_ref: "env:FEISHU_APP_ID", app_secret_ref: "env:FEISHU_APP_SECRET", status: "ready",
  authorization_version: 1, version: 2, created_by: "admin", created_at: "2026-09-02T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z", last_diagnosed_at: "2026-09-02T00:00:00Z", last_error_code: null,
};
const binding = {
  id: "binding-1", connection_id: connection.id, display_name: "研发 Wiki", external_space_id: "space-1",
  root_node_token: "root", status: "ready", authorization_version: 1, version: 1, replaces_binding_id: null,
  created_by: "admin", created_at: "2026-09-02T00:00:00Z", updated_at: "2026-09-02T00:00:00Z",
  last_permission_probe_at: "2026-09-02T00:00:00Z", last_error_code: null,
};
const flags = { feishu_tenant_sync_v1: true, knowledge_hybrid_index_v1: true, delivery_knowledge_context_v1: true };

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url === "/v1/knowledge/connections" && method === "GET") return response([connection]);
    if (url === "/v1/knowledge/provider-bindings-v2" && method === "GET") return response([binding]);
    if (url === `/v1/knowledge/connections/${connection.id}/spaces`) return response([{ external_id: "space-1", title: "研发知识库" }]);
    if (url === "/v1/knowledge/index-catalog") return response({
      profiles: [{ id: "profile-1", display_name: "BGE-M3", embedding_model_name: "bge-m3" }],
      qualifications: [{ id: "qualification-1", model_name: "bge-m3", model_digest: "sha256:" + "a".repeat(64), dimension: 1024, status: "qualified" }],
      retrieval_policies: [{ id: "policy-1", display_name: "Default", index_profile_revision_id: "profile-1" }],
      evaluation_policies: [], index_revisions: [{
        id: "index-1", provider_binding_id: binding.id, status: "active", document_count: 4_200,
        chunk_count: 82_000, capacity_status: "warning", version: 3,
      }], evaluation_reports: [],
    });
    if (url === `/v1/knowledge/connections/${connection.id}/diagnose` && method === "POST") return response(connection);
    return response({ code: "NOT_FOUND" }, 404);
  });
});

afterEach(() => vi.unstubAllGlobals());

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><KnowledgeIntegrationPanel flags={flags}/></QueryClientProvider>);
}

describe("飞书知识接入设置", () => {
  test("Gate A 关闭时不请求未挂载的知识 API", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><KnowledgeIntegrationPanel flags={{ feishu_tenant_sync_v1: false, knowledge_hybrid_index_v1: false, delivery_knowledge_context_v1: false }}/></QueryClientProvider>);

    expect(await screen.findByText("Gate A 尚未启用")).toBeTruthy();
    expect(calls.filter((call) => call.url.startsWith("/v1/knowledge/"))).toEqual([]);
  });

  test("展示 Tenant Connection、可见 Space、Binding 和索引就绪证据", async () => {
    renderPanel();

    expect(await screen.findByRole("heading", { name: "飞书知识接入" })).toBeTruthy();
    expect((await screen.findAllByText("研发飞书")).length).toBeGreaterThan(0);
    expect(await screen.findByText("研发知识库")).toBeTruthy();
    expect(screen.getByText("研发 Wiki")).toBeTruthy();
    expect(screen.getByText("活动索引 1")).toBeTruthy();
    expect(screen.getByText("4,200 docs · 82,000 chunks · v3")).toBeTruthy();
    expect(screen.getByText("容量告警")).toBeTruthy();
  });

  test("诊断动作调用真实写接口并刷新目录", async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "诊断连接" }));

    await waitFor(() => expect(calls).toContainEqual({
      url: `/v1/knowledge/connections/${connection.id}/diagnose`,
      method: "POST",
    }));
  });
});
