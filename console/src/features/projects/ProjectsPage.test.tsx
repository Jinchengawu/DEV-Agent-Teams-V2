// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage } from "./ProjectsPage";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const deployment = (id: string, name: string) => ({ id, name, profile_id: id, profile_revision: 1, profile_sha256: "a".repeat(64), capability_requirements: [], instance_id: "codex", instance_version: 1, adapter_id: "codex", adapter_version: "1", provider_id: "codex", provider_revision: "1", provider_fingerprint: "b".repeat(64), isolation_mode: "shared", policy_snapshot: {}, qualification_status: "qualified", qualification_errors: [], enabled: true, version: 1, created_by: "admin" });

describe("项目治理目录", () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/v1/projects" && (init?.method ?? "GET") === "GET") return response([]);
      if (url === "/v1/pipelines") return response([{ id: "delivery", name: "后端交付", description: "", active_revision: 3, version: 1, created_by: "admin" }]);
      if (url === "/v1/agent-deployments") return response([deployment("backend", "后端执行器")]);
      if (url === "/v1/projects" && init?.method === "POST") return response({ project: { id: "pj1", slug: "pj1", name: "项目一", description: "", lifecycle_status: "active", version: 2, created_by: "admin" }, workspace: { project_id: "pj1", workspace_id: "project:pj1", seed_revision: "c".repeat(40), repository_ref: "projects/pj1", status: "ready", provision_attempt: 1 }, pipeline_bindings: [], deployment_access: [], knowledge_sources: [], active_delivery_id: null }, 201);
      return response({}, 404);
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("创建项目时固定流水线版本和 Agent 部署授权", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects"]}><Routes><Route path="/projects" element={<ProjectsPage/>}/><Route path="/projects/:projectId/overview" element={<div>项目概览已打开</div>}/></Routes></MemoryRouter></QueryClientProvider>);
    await screen.findByText("还没有项目");
    await userEvent.type(screen.getByPlaceholderText("例如：pj1"), "pj1");
    await userEvent.type(screen.getByPlaceholderText("例如：客户门户后端"), "项目一");
    const pipelineSelect = screen.getByRole("combobox", { name: "默认流水线" });
    fireEvent.keyDown(pipelineSelect, { key: "ArrowDown", code: "ArrowDown", keyCode: 40 });
    fireEvent.keyDown(pipelineSelect, { key: "Enter", code: "Enter", keyCode: 13 });
    await userEvent.click(screen.getByRole("checkbox", { name: /后端执行器/ }));
    await userEvent.click(screen.getByRole("button", { name: "创建并初始化独立工作区" }));
    await screen.findByText("项目概览已打开");
    const create = calls.find((call) => call.url === "/v1/projects" && call.init?.method === "POST");
    expect(create).toBeTruthy();
    expect(JSON.parse(String(create?.init?.body))).toMatchObject({ id: "pj1", default_pipeline_revision_id: "delivery:3", deployment_ids: ["backend"] });
    await waitFor(() => expect(calls.some((call) => call.url === "/v1/projects")).toBe(true));
  });

  it("Deployment 目录晚于流水线返回时仍自动选择五角色部署", async () => {
    let resolveDeployments!: (value: Response) => void;
    const delayedDeployments = new Promise<Response>((resolve) => { resolveDeployments = resolve; });
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url === "/v1/projects" && (init?.method ?? "GET") === "GET") return response([]);
      if (url === "/v1/pipelines") return response([{ id: "fullstack-product-delivery", name: "产品规划 → UI 设计 → 前后端 → 测试发布", description: "", active_revision: 1, version: 1, created_by: "system" }]);
      if (url === "/v1/agent-deployments") return delayedDeployments;
      return response({}, 404);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects"]}><Routes><Route path="/projects" element={<ProjectsPage/>}/></Routes></MemoryRouter></QueryClientProvider>);
    await screen.findByText("还没有项目");
    const pipelineSelect = screen.getByRole("combobox", { name: "默认流水线" });
    fireEvent.keyDown(pipelineSelect, { key: "ArrowDown", code: "ArrowDown", keyCode: 40 });
    fireEvent.keyDown(pipelineSelect, { key: "Enter", code: "Enter", keyCode: 13 });
    resolveDeployments(response([
      deployment("builtin-planning-deployment", "规划 Agent"),
      deployment("builtin-design-deployment", "设计 Agent"),
      deployment("builtin-backend-deployment", "后端 Agent"),
      deployment("builtin-frontend-deployment", "前端 Agent"),
      deployment("builtin-qa-deployment", "测试 Agent"),
    ]));
    await waitFor(() => {
      expect(screen.getAllByRole("checkbox").filter((item) => (item as HTMLInputElement).checked)).toHaveLength(5);
    });
  });
});
