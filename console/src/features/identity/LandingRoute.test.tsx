// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { LandingRoute } from "./LandingRoute";

afterEach(() => { vi.unstubAllGlobals(); window.localStorage.clear(); });

it("忽略 legacy 默认项目并把 ready 项目定向到交付工作台", async () => {
  vi.stubGlobal("fetch", async (input: RequestInfo) => {
    const url = String(input);
    if (url === "/v1/projects") return response([{ id: "legacy-default", name: "Legacy" }, { id: "guided", name: "Guided" }]);
    if (url === "/v1/projects/guided") return response({ project: { id: "guided" }, workspace: {}, onboarding: { status: "ready", version: 3 } });
    if (url === "/v1/setup-readiness?project_id=guided") return response({ status: "ready", project_id: "guided", global_checks: [], project_checks: [], optional_checks: [] });
    return response({}, 404);
  });
  renderRoute();
  expect(await screen.findByText("交付工作台 guided")).toBeTruthy();
});

it("准备度未通过时进入准备中心", async () => {
  vi.stubGlobal("fetch", async (input: RequestInfo) => {
    const url = String(input);
    if (url === "/v1/projects") return response([{ id: "guided", name: "Guided" }]);
    if (url === "/v1/projects/guided") return response({ project: { id: "guided" }, workspace: {}, onboarding: { status: "setup", version: 1 } });
    if (url === "/v1/setup-readiness?project_id=guided") return response({ status: "blocked", project_id: "guided", global_checks: [], project_checks: [], optional_checks: [] });
    return response({}, 404);
  });
  renderRoute();
  expect(await screen.findByText("准备中心落点")).toBeTruthy();
});

function renderRoute() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/"]}><Routes>
    <Route path="/" element={<LandingRoute/>}/>
    <Route path="/setup" element={<div>准备中心落点</div>}/>
    <Route path="/projects/:projectId/deliveries" element={<div>交付工作台 guided</div>}/>
  </Routes></MemoryRouter></QueryClientProvider>);
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
