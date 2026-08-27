// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { KnowledgePage } from "./KnowledgePage";

type Call = { url: string; method: string };

type Doc = { id: string; space_id: string; title: string; current_revision: number; version: number };
type Revision = { document_id: string; revision: number; content: unknown; content_sha256: string; provenance: Record<string, unknown>; created_at: string };
type Space = { id: string; name: string; description: string; space_kind: string; lifecycle_status: string; version: number; created_by: string; created_at: string; updated_at: string };
type Comment = { id: string; document_id: string; body: string; author_id: string; resolved: boolean; version: number; created_at: string; updated_at: string };

const spaces: Space[] = [
  { id: "space-custom", name: "自定义经验库", description: "次级入口", space_kind: "custom", lifecycle_status: "active", version: 1, created_by: "ops", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" },
  { id: "space-1", name: "项目文档", description: "标准项目空间", space_kind: "project-documents", lifecycle_status: "active", version: 1, created_by: "ops", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" },
];

const documents: Doc[] = [
  { id: "doc-1", space_id: "space-1", title: "设计说明文档", current_revision: 1, version: 3 },
];

const revision: Revision = {
  document_id: "doc-1",
  revision: 1,
  content: {
    schema: "project-document-v1",
    artifact_key: "primary",
    markdown: "# 需求\\n请使用中文",
  },
  content_sha256: "a".repeat(64),
  provenance: {
    producer_kind: "agent",
    producer_id: "product-manager",
    agent_run_id: "run-pm-1",
    runtime_identity: "codex-simulated-hermes",
    source_artifact_sha256: "b".repeat(64),
  },
  created_at: "2026-08-02T00:00:00Z",
};

const fetchCalls: Call[] = [];

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const route = (url: string, method: string) => {
  if (url.startsWith("/v1/wiki/spaces?")) return jsonResponse(spaces);
  if (url.startsWith("/v1/wiki/search")) return jsonResponse(documents);
  if (url.startsWith("/v1/wiki/documents?space_id=space-1")) return jsonResponse(documents);
  if (url.startsWith("/v1/wiki/documents?space_id=space-custom")) return jsonResponse([]);
  if (url.startsWith("/v1/knowledge/search?")) return jsonResponse([
    { project_id: "legacy-default", group: "project-document", source_kind: "wiki", source_id: "doc-1", title: "产品需求", summary: "角色协作文档", revision: "1", content_sha256: "a".repeat(64), source_link: "/projects/legacy-default/knowledge?document_id=doc-1" },
    { project_id: "legacy-default", group: "evidence", source_kind: "evidence", source_id: "ev-1", title: "Verification · delivery-1", summary: "verification · verified", revision: "1", content_sha256: "c".repeat(64), source_link: "/projects/legacy-default/evidence?evidence_id=ev-1" },
    { project_id: "legacy-default", group: "external-source", source_kind: "provider-snapshot", source_id: "snapshot-1", title: "Feishu · PRD", summary: "授权快照", revision: "r1", content_sha256: "d".repeat(64), source_link: "https://example.com/prd" },
  ]);
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
  cleanup();
  vi.unstubAllGlobals();
});

describe("知识空间三栏页", () => {
  test("使用真实 /v1/wiki 接口读取空间与文档", async () => {
    renderKnowledge();
    await waitFor(() => expect(fetchCalls.some((call) => call.url.startsWith("/v1/wiki/spaces?project_id=legacy-default"))).toBe(true));
    await waitFor(() => expect(fetchCalls.some((call) => call.url.includes("/v1/wiki/documents"))).toBe(true));
    expect(fetchCalls.every((call) => !call.url.includes("/v1/knowledge/"))).toBe(true);
    expect(fetchCalls.some((call) => call.url.startsWith("/v1/wiki/documents?space_id=space-1"))).toBe(true);
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
    expect(screen.getByText(/AgentRun run-pm-1/)).toBeTruthy();
    expect(screen.getByText(/原始 Artifact SHA-256/)).toBeTruthy();
    expect((screen.getByPlaceholderText("Markdown 正文") as HTMLTextAreaElement).value).toBe("# 需求\\n请使用中文");
    expect(screen.queryByText(/project-document-v1/)).toBeNull();
  });

  test("按文档类型、角色、Delivery 与来源筛选标准项目空间", async () => {
    renderKnowledge();
    await screen.findByRole("button", { name: "文档 设计说明文档" });
    const filters = within(screen.getByLabelText("项目文档筛选"));
    await userEvent.selectOptions(filters.getByLabelText("文档类型"), "product-requirement");
    await userEvent.type(filters.getByLabelText("角色"), "product-manager");
    await userEvent.type(filters.getByLabelText("Delivery"), "delivery-1");
    await userEvent.selectOptions(filters.getByLabelText("来源"), "agent-publication");
    await waitFor(() => expect(fetchCalls.some((call) =>
      call.url.includes("document_kind=product-requirement")
      && call.url.includes("role_key=product-manager")
      && call.url.includes("delivery_id=delivery-1")
      && call.url.includes("source_kind=agent-publication"),
    )).toBe(true));
  });

  test("统一搜索按项目文档、Evidence 与外部来源分组", async () => {
    const view = renderKnowledge();
    await within(view.container).findByRole("button", { name: "文档 设计说明文档" });
    await userEvent.type(within(view.container).getByLabelText("全文搜索"), "需求");
    await within(view.container).findByLabelText("项目文档搜索结果");
    expect(within(view.container).getByLabelText("Evidence搜索结果")).toBeTruthy();
    expect(within(view.container).getByLabelText("外部来源搜索结果")).toBeTruthy();
    expect(within(view.container).getByRole("link", { name: /Verification/ }).getAttribute("href")).toContain("/evidence?");
  });

  test("创建文档后在列表重新验证前保持选中并读取当前修订", async () => {
    const createdDocument: Doc = {
      id: "doc-2",
      space_id: "space-1",
      title: "新建协议文档",
      current_revision: 1,
      version: 1,
    };
    const createdRevision: Revision = {
      document_id: "doc-2",
      revision: 1,
      content: { format: "markdown", text: "# 新建内容" },
      content_sha256: "b".repeat(64),
      provenance: { producer_kind: "human", producer_id: "ops" },
      created_at: "2026-08-03T00:00:00Z",
    };
    const createdComment: Comment = {
      id: "comment-2",
      document_id: "doc-2",
      body: "待处理评论",
      author_id: "ops",
      resolved: false,
      version: 1,
      created_at: "2026-08-03T00:00:00Z",
      updated_at: "2026-08-03T00:00:00Z",
    };
    let documentListRequestCount = 0;
    let resolveRevalidation!: (response: Response) => void;
    const pendingRevalidation = new Promise<Response>((resolve) => {
      resolveRevalidation = resolve;
    });

    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      fetchCalls.push({ url, method });

      if (url === "/v1/wiki/documents?space_id=space-1") {
        documentListRequestCount += 1;
        return documentListRequestCount === 1 ? jsonResponse(documents) : pendingRevalidation;
      }
      if (url === "/v1/wiki/documents" && method === "POST") return jsonResponse(createdDocument);
      if (url === "/v1/wiki/documents/doc-2/revisions/1") return jsonResponse(createdRevision);
      if (url === "/v1/wiki/documents/doc-2/revisions") return jsonResponse([createdRevision]);
      if (url === "/v1/wiki/documents/doc-2/comments") return jsonResponse([createdComment]);
      return route(url, method);
    });

    const view = renderKnowledge();
    const page = within(view.container);
    await page.findByRole("button", { name: "文档 设计说明文档" });
    await userEvent.type(page.getByPlaceholderText("文档标题"), createdDocument.title);
    await userEvent.type(page.getByPlaceholderText("使用 Markdown 正文"), "# 新建内容");
    await userEvent.click(page.getByRole("button", { name: "创建文档" }));

    const createdDocumentButton = await page.findByRole("button", { name: `文档 ${createdDocument.title}` });
    expect(createdDocumentButton.className).toContain("selected");
    await waitFor(() =>
      expect(fetchCalls.some((call) => call.url === "/v1/wiki/documents/doc-2/revisions/1")).toBe(true),
    );
    await page.findByRole("heading", { name: createdDocument.title });
    await page.findByText("修订 1");
    await page.findByText(/\u5904\u7406\u72b6\u6001 \u672a\u89e3\u6790/);
    expect(page.queryByText(/resolved/)).toBeNull();

    resolveRevalidation(jsonResponse(documents));
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
