// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { KnowledgePage } from "./KnowledgePage";

type Call = { url: string; method: string };

type Doc = { id: string; space_id: string; title: string; current_revision: number; version: number };
type Revision = { document_id: string; revision: number; content: unknown; content_sha256: string; created_at: string };
type Space = { id: string; name: string; description: string; version: number; created_by: string; created_at: string; updated_at: string };
type Comment = { id: string; document_id: string; body: string; author_id: string; resolved: boolean; version: number; created_at: string; updated_at: string };

const spaces: Space[] = [
  { id: "space-1", name: "交付知识", description: "交付知识基础空间", version: 1, created_by: "ops", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" },
];

const documents: Doc[] = [
  { id: "doc-1", space_id: "space-1", title: "设计说明文档", current_revision: 1, version: 3 },
];

const revision: Revision = {
  document_id: "doc-1",
  revision: 1,
  content: { format: "markdown", text: "# 需求\\n请使用中文" },
  content_sha256: "a".repeat(64),
  created_at: "2026-08-02T00:00:00Z",
};

const fetchCalls: Call[] = [];

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const route = (url: string, method: string) => {
  if (url === "/v1/wiki/spaces") return jsonResponse(spaces);
  if (url.startsWith("/v1/wiki/search")) return jsonResponse(documents);
  if (url === "/v1/wiki/documents?space_id=space-1") return jsonResponse(documents);
  if (url === "/v1/wiki/documents/doc-1/revisions/1") return jsonResponse(revision);
  if (url === "/v1/wiki/documents/doc-1/revisions") return jsonResponse([revision]);
  if (url === "/v1/wiki/documents/doc-1/comments") return jsonResponse([] as Comment[]);
  if (url === "/v1/wiki/documents/doc-1" && method === "PATCH") {
    return new Response(
      JSON.stringify({
        code: "conflict",
        title: "资源冲突",
        detail: "版本已变化",
        repair: "刷新当前数据后重试。",
      }),
      { status: 409, headers: { "content-type": "application/json" } },
    );
  }
  if (url === "/v1/wiki/spaces" || url === "/v1/wiki/documents") {
    return jsonResponse([]);
  }
  return new Response(JSON.stringify({}), { status: 404, headers: { "content-type": "application/json" } });
};

const renderKnowledge = () => {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={client}>
      <KnowledgePage />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  fetchCalls.length = 0;
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    fetchCalls.push({ url, method });
    return route(url, method);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("知识空间三栏页", () => {
  test("使用真实 /v1/wiki 接口读取空间与文档", async () => {
    renderKnowledge();
    await waitFor(() => expect(fetchCalls.some((call) => call.url === "/v1/wiki/spaces")).toBe(true));
    await waitFor(() => expect(fetchCalls.some((call) => call.url.includes("/v1/wiki/documents"))).toBe(true));
    expect(fetchCalls.every((call) => !call.url.includes("/v1/knowledge/"))).toBe(true);
  });

  test("选择文档后读取当前修订正文", async () => {
    renderKnowledge();
    const [documentButton] = await screen.findAllByRole("button", { name: "文档 设计说明文档" });
    await userEvent.click(documentButton);
    await waitFor(() => expect(fetchCalls.some((call) => call.url === "/v1/wiki/documents/doc-1/revisions/1")).toBe(true));
    await screen.findByRole("heading", { name: "设计说明文档" });
    await waitFor(() =>
      expect(
        screen.getAllByText((_content, element) => {
          return Boolean(element?.tagName === "P" && element.textContent?.startsWith("修订号:"));
        }).length,
      ).toBeGreaterThan(0),
    );
    await screen.findByText(/内容 SHA-256/);
  });

  test("空状态展示可见中文提示", async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", async () => jsonResponse([]));
    renderKnowledge();
    await screen.findByText("当前无知识空间");
    expect(screen.getByText("请先在下方创建知识空间后再写入文档。")).toBeTruthy();
  });

  test("409 冲突只显示刷新后重试提示", async () => {
    renderKnowledge();
    const [documentButton] = await screen.findAllByRole("button", { name: "文档 设计说明文档" });
    await userEvent.click(documentButton);
    const saveButton = await screen.findByRole("button", { name: "保存标题与正文" });
    await userEvent.click(saveButton);
    await waitFor(() => expect(screen.getAllByText("刷新当前数据后重试。").length).toBeGreaterThan(0));
    expect(fetchCalls.some((call) => call.url === "/v1/wiki/documents/doc-1" && call.method === "PATCH")).toBe(true);
  });
});
