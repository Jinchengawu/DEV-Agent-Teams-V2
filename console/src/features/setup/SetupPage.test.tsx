// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { SetupPage } from "./SetupPage";

afterEach(() => vi.unstubAllGlobals());

it("使用可见的 project_id 深链并展示同源阻塞", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo) => {
    const url = String(input); calls.push(url);
    if (url === "/v1/projects") return response([{ id: "alpha", name: "Alpha" }, { id: "beta", name: "Beta" }]);
    if (url === "/v1/setup-readiness?project_id=beta") return response({
      status: "blocked", project_id: "beta",
      global_checks: [{ id: "runtime.codex-cli", status: "blocked", summary: "Codex CLI 未就绪", repair: "安装并登录 Codex CLI。", navigation_target: "/settings", blocking_scope: "start_delivery" }],
      project_checks: [], optional_checks: [],
    });
    return response({}, 404);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/setup?project_id=beta"]}><SetupPage/></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText("Codex CLI 未就绪")).toBeTruthy();
  expect(screen.getByText("准备中心 · beta")).toBeTruthy();
  expect(calls).toContain("/v1/setup-readiness?project_id=beta");
  expect(screen.getByRole("link", { name: "打开 Codex CLI 未就绪 修复入口" }).getAttribute("href")).toBe("/settings");
});

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
