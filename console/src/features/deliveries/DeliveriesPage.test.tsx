// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeliveriesPage } from "./DeliveriesPage";

type FetchCall = { url: string; method: string; body?: string };

const calls: FetchCall[] = [];
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" },
});

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const call = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: init?.body?.toString(),
    };
    calls.push(call);
    if (call.url === "/v1/deliveries?project_id=pj1" && call.method === "GET") return response([]);
    if (call.url === "/v1/projects/pj1" && call.method === "GET") return response({
      project: { id: "pj1", name: "项目一", description: "", lifecycle_status: "active", version: 1 },
      workspace: { workspace_id: "project:pj1", status: "ready" },
      pipeline_bindings: [{ pipeline_id: "backend-delivery", pipeline_revision: 1, enabled: true, is_default: true }],
      deployment_access: [{ deployment_id: "builtin-backend-deployment", enabled: true }],
      knowledge_sources: [],
      active_delivery_id: null,
    });
    if (call.url === "/v1/pipelines" && call.method === "GET") {
      return response([{ id: "backend-delivery", name: "内置后端交付闭环", active_revision: 1 }]);
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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/pj1/deliveries"]}><Routes><Route path="/projects/:projectId/deliveries" element={<DeliveriesPage/>}/><Route path="/projects/:projectId/deliveries/:deliveryId" element={<div>交付详情</div>}/></Routes></MemoryRouter>
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
    });
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
});
