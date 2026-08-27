// @vitest-environment jsdom
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectOverviewPage } from "./ProjectOverviewPage";

const projectDetail = {
  project: { id: "pj1", slug: "pj1", name: "项目一", description: "验收项目", lifecycle_status: "active", version: 2, created_by: "admin", created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" },
  workspace: { project_id: "pj1", workspace_id: "project:pj1", seed_revision: "c".repeat(40), repository_ref: "projects/pj1", status: "ready", provision_attempt: 1, error_code: null, created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" },
  pipeline_bindings: [],
  deployment_access: [],
  knowledge_sources: [],
  active_delivery_id: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("项目概览", () => {
  it("从异步加载进入项目页面时保持 Hooks 顺序稳定", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

    render(<StrictMode><QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/pj1/overview"]}><Routes><Route path="/projects/:projectId/overview" element={<ProjectOverviewPage/>}/></Routes></MemoryRouter></QueryClientProvider></StrictMode>);
    expect(screen.getByText("正在读取项目执行上下文…")).toBeTruthy();

    resolve(new Response(JSON.stringify(projectDetail), { status: 200, headers: { "content-type": "application/json" } }));
    expect(await screen.findByRole("heading", { name: "项目一" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "重置项目工作区" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
