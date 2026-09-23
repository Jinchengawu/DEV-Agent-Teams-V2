// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { KnowledgeContextPanel } from "./KnowledgeContextPanel";

const now = "2026-09-02T00:00:00Z";
const hash = "b".repeat(64);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("交付运行室展示冻结知识、不可用回执和 Workcell 引用投影", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({
    delivery_id: "delivery-1",
    delivery_status: "executing",
    preparation_run: {
      id: "prep-1",
      delivery_id: "delivery-1",
      input_sha256: hash,
      knowledge_binding_hash: hash,
      preparation_input: { schema: "knowledge-preparation-input-v1" },
      status: "succeeded",
      attempt_count: 1,
      authorization_epoch_hash: hash,
      created_at: now,
      updated_at: now,
    },
    contexts: [{
      stage_path: "design-repair/design",
      artifact_reference: { uri: `artifact://sha256/${hash}`, sha256: hash, media_type: "application/vnd.agent-team-os.knowledge-context+json", size_bytes: 420 },
      citation_ids: ["CIT-1"],
      authorization_epoch_hash: hash,
      trust_class: "external-collaborative",
    }],
    unavailable: [{
      stage_path: "backend-repair/backend",
      receipt_reference: { uri: `artifact://sha256/${hash}`, sha256: hash, media_type: "application/vnd.agent-team-os.knowledge-context-unavailable+json", size_bytes: 80 },
      error_code: "KNOWLEDGE_SOURCE_REVOKED",
    }],
    citations: [{ citation_id: "CIT-1", stage_paths: ["design-repair/design"], workcell_run_ids: ["workcell-1"] }],
  }), { status: 200, headers: { "content-type": "application/json" } }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KnowledgeContextPanel projectId="project-1" deliveryId="delivery-1"/></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "Delivery Knowledge Context" })).toBeTruthy();
  expect((await screen.findAllByText("design-repair/design")).length).toBeGreaterThan(0);
  expect(await screen.findByText("KNOWLEDGE_SOURCE_REVOKED")).toBeTruthy();
  expect(await screen.findByText("workcell-1")).toBeTruthy();
});

test("同页运行时刷新新生成的 Context 和引用，终态后停止轮询", async () => {
  let requests = 0;
  vi.stubGlobal("fetch", async () => {
    requests += 1;
    return new Response(JSON.stringify({
      delivery_id: "delivery-progress",
      delivery_status: requests >= 3 ? "completed" : "executing",
      preparation_run: null,
      contexts: requests >= 2 ? [{
        stage_path: "backend-repair/backend",
        artifact_reference: { uri: `artifact://sha256/${hash}`, sha256: hash, media_type: "application/json", size_bytes: 42 },
        citation_ids: ["CIT-PROGRESS"], authorization_epoch_hash: hash,
        trust_class: "external-collaborative",
      }] : [],
      unavailable: [],
      citations: requests >= 3 ? [{ citation_id: "CIT-PROGRESS", stage_paths: ["backend-repair/backend"], workcell_run_ids: ["workcell-progress"] }] : [],
    }), { status: 200, headers: { "content-type": "application/json" } });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><KnowledgeContextPanel projectId="project-progress" deliveryId="delivery-progress"/></QueryClientProvider>);
  expect(await screen.findByText("该 Delivery 未要求外部知识上下文")).toBeTruthy();
  expect(await screen.findByText("workcell-progress", {}, { timeout: 3500 })).toBeTruthy();
  const completedRequests = requests;
  await new Promise((resolve) => setTimeout(resolve, 1200));
  expect(requests).toBe(completedRequests);
  view.unmount();
  client.clear();
});

test.each(["KNOWLEDGE_QUERY_INPUT_UNQUALIFIED", "KNOWLEDGE_QUERY_BUDGET_EXCEEDED", "KNOWLEDGE_OLLAMA_REQUEST_FAILED"])("%s 提供安全只读指导且不把失败说成冻结成功", async (errorCode) => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    requests.push(init?.method ?? "GET");
    return new Response(JSON.stringify({
      delivery_id: "delivery-failed", delivery_status: "failed",
      preparation_run: {
        id: "prep-failed", status: "failed", input_sha256: hash,
        knowledge_binding_hash: hash, authorization_epoch_hash: null,
        attempt_count: 1, error_code: errorCode,
      },
      contexts: [], unavailable: [], citations: [],
      query_errors: [{
        code: "KNOWLEDGE_QUERY_INPUT_LIMIT", category: "input_limit", http_status: 400,
        unit: 0, measured_tokens: 9000, allowed_tokens: 8192,
        measured_bytes: 12000, allowed_bytes: 16384,
        correlation_id: "24e9acce-3d80-446f-a4a0-46ae057bef49",
        provider_body: "PRIVATE_PROVIDER_BODY", query: "PRIVATE_QUERY_TEXT",
        credential: "PRIVATE_CREDENTIAL",
      }],
    }), { status: 200, headers: { "content-type": "application/json" } });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KnowledgeContextPanel projectId="project-viewer" deliveryId="delivery-failed"/></QueryClientProvider>);
  expect(await screen.findByText("输入资格与预算检查未通过，或模型请求失败")).toBeTruthy();
  expect(screen.getByText(/不会截断原始需求，也不会自动重试输入限制错误/)).toBeTruthy();
  expect(screen.getByText(/终态不会在此恢复；新交付需另行授权/)).toBeTruthy();
  expect(screen.queryByText(/所有声明输入均已冻结/)).toBeNull();
  expect(screen.getByText(/没有不可用回执不代表输入已成功冻结/)).toBeTruthy();
  expect(screen.getByText("9000 / 8192 tokens")).toBeTruthy();
  expect(screen.getByText("12000 / 16384 bytes")).toBeTruthy();
  expect(screen.getByText("HTTP 400 · input_limit · Unit 0")).toBeTruthy();
  expect(screen.queryByText(/PRIVATE_/)).toBeNull();
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  expect(requests.every((method) => method === "GET")).toBe(true);
  client.clear();
});

test("非输入错误不误导为模型输入超限，也不声称已冻结", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({
    delivery_id: "delivery-other", delivery_status: "failed",
    preparation_run: { id: "prep-other", status: "failed", input_sha256: hash,
      knowledge_binding_hash: hash, attempt_count: 1, error_code: "KNOWLEDGE_SOURCE_REVOKED" },
    contexts: [], unavailable: [], citations: [],
  }), { status: 200, headers: { "content-type": "application/json" } }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KnowledgeContextPanel projectId="project-1" deliveryId="delivery-other"/></QueryClientProvider>);
  expect(await screen.findByText("KNOWLEDGE_SOURCE_REVOKED")).toBeTruthy();
  expect(screen.queryByText("输入资格与预算检查未通过，或模型请求失败")).toBeNull();
  expect(screen.queryByText(/所有声明输入均已冻结/)).toBeNull();
  client.clear();
});
