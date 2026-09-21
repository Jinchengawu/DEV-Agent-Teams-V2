// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeliveriesPage } from "./DeliveriesPage";
import { IdentityProvider } from "../identity/AuthGate";

type FetchCall = { url: string; method: string; body?: string };

const calls: FetchCall[] = [];
let projectDetail: Record<string, unknown>;
let pipelineCatalog: Array<Record<string, unknown>>;
let setupReport: Record<string, unknown>;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" },
});

beforeEach(() => {
  window.localStorage.clear();
  calls.length = 0;
  projectDetail = {
    project: { id: "pj1", name: "项目一", description: "", lifecycle_status: "active", version: 1 },
    workspace: { workspace_id: "project:pj1", repository_ref: "projects/pj1", status: "ready" },
    pipeline_bindings: [{ pipeline_id: "backend-delivery", pipeline_revision: 1, enabled: true, is_default: true }],
    deployment_access: [{ deployment_id: "builtin-backend-deployment", enabled: true }],
    knowledge_sources: [],
    active_delivery_id: null,
    onboarding: { project_id: "pj1", mode: "standard", status: "ready", evaluation_delivery_id: null, version: 1 },
  };
  pipelineCatalog = [{ id: "backend-delivery", name: "内置后端交付闭环", active_revision: 1 }];
  setupReport = { status: "ready", project_id: "pj1", global_checks: [], project_checks: [], optional_checks: [] };
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const call = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: init?.body?.toString(),
    };
    calls.push(call);
    if (call.url === "/v1/deliveries?project_id=pj1" && call.method === "GET") return response([]);
    if (call.url === "/v1/projects/pj1" && call.method === "GET") return response(projectDetail);
    if (call.url === "/v1/setup-readiness?project_id=pj1" && call.method === "GET") return response(setupReport);
    if (call.url === "/v1/pipelines" && call.method === "GET") {
      return response(pipelineCatalog);
    }
    if (call.url === "/v1/deliveries" && call.method === "POST") {
      return response({
        id: "delivery-created",
        project_id: "pj1",
        workspace_id: "project:pj1",
        user_request: "增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。",
        status: "queued",
        version: 1,
      }, 202);
    }
    return response({}, 404);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage(role: "administrator" | "editor" | "viewer" = "administrator") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdentityProvider user={{ id: `${role}-1`, username: role, display_name: role, role, enabled: true, authorization_version: 1, version: 1, created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z" }}>
        <MemoryRouter initialEntries={["/projects/pj1/deliveries"]}><Routes><Route path="/projects/:projectId/deliveries" element={<DeliveriesPage/>}/><Route path="/projects/:projectId/deliveries/:deliveryId" element={<div>交付详情</div>}/></Routes></MemoryRouter>
      </IdentityProvider>
    </QueryClientProvider>,
  );
}

describe("交付工作台", () => {
  it("先确认真实边界，再向 API 创建交付", async () => {
    renderPage();
    const generate = await screen.findByRole("button", { name: "生成交付计划" });

    await userEvent.click(generate);
    expect(screen.getByRole("heading", { name: "目标与执行边界" })).toBeTruthy();
    expect(calls.filter((call) => call.url === "/v1/deliveries" && call.method === "POST")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "确认并启动" }));
    await waitFor(() => expect(calls.some((call) => call.url === "/v1/deliveries" && call.method === "POST")).toBe(true));
    const created = calls.find((call) => call.url === "/v1/deliveries" && call.method === "POST");
    expect(JSON.parse(created?.body ?? "{}")).toEqual({
      project_id: "pj1",
      user_request: "增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。",
      pipeline_revision_id: "backend-delivery:1",
      purpose: "product",
    });
  });

  it("使用项目明确设置的默认 Pipeline，而不是目录排序后的第一条", async () => {
    projectDetail = {
      ...projectDetail,
      pipeline_bindings: [
        { pipeline_id: "agent-workcell-delivery", pipeline_revision: 2, enabled: true, is_default: false },
        { pipeline_id: "fullstack-product-delivery", pipeline_revision: 3, enabled: true, is_default: true },
      ],
    };
    pipelineCatalog = [
      { id: "agent-workcell-delivery", name: "Agent Workcell Delivery", active_revision: 2 },
      { id: "fullstack-product-delivery", name: "全栈产品交付", active_revision: 3 },
    ];

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "生成交付计划" }));
    expect(screen.getAllByText("全栈产品交付 · R3")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "确认并启动" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/deliveries" && call.method === "POST")).toBe(true));
    const created = calls.find((call) => call.url === "/v1/deliveries" && call.method === "POST");
    expect(JSON.parse(created?.body ?? "{}").pipeline_revision_id).toBe("fullstack-product-delivery:3");
  });

  it("空目标会在本地阻断，并把焦点返回目标输入框", async () => {
    renderPage();
    const goal = await screen.findByLabelText("交付目标");
    await userEvent.clear(goal);
    await userEvent.click(screen.getByRole("button", { name: "生成交付计划" }));

    expect(screen.getByRole("alert").textContent).toContain("请输入边界清晰的交付目标");
    expect(document.activeElement).toBe(goal);
    expect(calls.filter((call) => call.url === "/v1/deliveries" && call.method === "POST")).toHaveLength(0);
  });

  it("Workcell 项目展示四仓与冻结 Slot 语义，不回退为单仓 CAS", async () => {
    projectDetail = {
      ...projectDetail,
      workspace: {
        workspace_id: "project:pj1",
        repository_ref: "workspace-set/pj1",
        status: "ready",
      },
      pipeline_bindings: [{ pipeline_id: "agent-workcell-delivery", pipeline_revision: 1, enabled: true, is_default: true }],
      deployment_access: [],
    };
    pipelineCatalog = [{ id: "agent-workcell-delivery", name: "Agent Workcell Delivery", active_revision: 1 }];

    renderPage();

    expect(await screen.findByText("Repository Workcell Set")).toBeTruthy();
    expect(screen.getByText(/跨 Workcell 只传 Artifact/)).toBeTruthy();
    expect(screen.getByText("Pipeline 冻结 Slot")).toBeTruthy();
    expect(screen.queryByText("独立 Git Main，候选应用受 CAS 保护")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "生成交付计划" }));
    expect(screen.getByText("四个隔离 Repository Workcell · 计划 / 设计 / 发布 Gate")).toBeTruthy();
  });

  it("准备度阻塞时保留边界预览但不发送创建请求", async () => {
    setupReport = { status: "blocked", project_id: "pj1", global_checks: [{ id: "runtime.codex-cli", status: "blocked", summary: "Codex CLI 未就绪", repair: "登录", navigation_target: "/settings", blocking_scope: "start_delivery" }], project_checks: [], optional_checks: [] };
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "生成交付计划" }));
    const start = screen.getByRole("button", { name: "确认并启动" });
    expect((start as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Codex CLI 未就绪")).toBeTruthy();
    expect(calls.filter((call) => call.url === "/v1/deliveries" && call.method === "POST")).toHaveLength(0);
  });

  it("准备度请求失败时 fail-closed，刷新后恢复草稿并允许确认", async () => {
    setupReport = {};
    let readinessFailed = true;
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body?.toString() });
      if (url === "/v1/deliveries?project_id=pj1") return response([]);
      if (url === "/v1/projects/pj1") return response(projectDetail);
      if (url === "/v1/pipelines") return response(pipelineCatalog);
      if (url === "/v1/setup-readiness?project_id=pj1") {
        if (readinessFailed) { readinessFailed = false; return response({ detail: "temporary" }, 503); }
        return response({ status: "ready", project_id: "pj1", global_checks: [], project_checks: [], optional_checks: [] });
      }
      return response({}, 404);
    });
    window.localStorage.setItem("agent-team-os.delivery-draft.pj1", "恢复后仍保留的交付目标");
    const first = renderPage();
    expect(await screen.findByText("准备度请求失败；系统已 fail-closed。")).toBeTruthy();
    expect((screen.getByLabelText("交付目标") as HTMLTextAreaElement).value).toBe("恢复后仍保留的交付目标");
    await userEvent.click(screen.getByRole("button", { name: "生成交付计划" }));
    expect((screen.getByRole("button", { name: "确认并启动" }) as HTMLButtonElement).disabled).toBe(true);
    first.unmount();
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "生成交付计划" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "确认并启动" }) as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByLabelText("交付目标") as HTMLTextAreaElement).value).toBe("恢复后仍保留的交付目标");
  });

  it("Viewer 只显示只读运行，不显示创建动作", async () => {
    renderPage("viewer");
    expect(await screen.findByRole("region", { name: "只读交付权限" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "生成交付计划" })).toBeNull();
    expect(calls.filter((call) => call.url === "/v1/deliveries" && call.method === "POST")).toHaveLength(0);
  });

  it("缺失 Pipeline 时禁止进入确认流程", async () => {
    projectDetail = { ...projectDetail, pipeline_bindings: [], deployment_access: [] };
    pipelineCatalog = [];
    setupReport = { status: "blocked", project_id: "pj1", global_checks: [], project_checks: [{ id: "project.pipeline", status: "blocked", summary: "项目缺少默认 Pipeline", repair: "绑定 Pipeline", navigation_target: "/pipelines", blocking_scope: "start_delivery" }], optional_checks: [] };
    renderPage();
    expect(await screen.findByText("没有已激活 Pipeline")).toBeTruthy();
    expect((screen.getByRole("button", { name: "生成交付计划" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
