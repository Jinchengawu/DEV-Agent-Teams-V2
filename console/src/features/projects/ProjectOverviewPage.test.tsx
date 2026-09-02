// @vitest-environment jsdom
import { StrictMode, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectOverviewPage } from "./ProjectOverviewPage";
import { IdentityProvider } from "../identity/AuthGate";

const projectDetail = {
  project: { id: "pj1", slug: "pj1", name: "项目一", description: "验收项目", lifecycle_status: "active", version: 2, created_by: "admin", created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" },
  workspace: { project_id: "pj1", workspace_id: "project:pj1", seed_revision: "c".repeat(40), repository_ref: "projects/pj1", status: "ready", provision_attempt: 1, created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" },
  pipeline_bindings: [], deployment_access: [], knowledge_sources: [], active_delivery_id: null,
};
const evaluator = { id: "user-1", username: "evaluator", display_name: "评测管理员", role: "administrator", enabled: true, authorization_version: 1, version: 1, created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" } as const;

function withIdentity(children: ReactNode) {
  return <IdentityProvider user={evaluator}>{children}</IdentityProvider>;
}

afterEach(() => vi.unstubAllGlobals());

describe("项目概览", () => {
  it("从异步加载过渡到可操作页面时保持 Hooks 顺序稳定", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo) => String(input) === "/v1/projects/pj1" ? new Promise<Response>((done) => { resolve = done; }) : Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } }))));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<StrictMode><QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/pj1/overview"]}><Routes><Route path="/projects/:projectId/overview" element={withIdentity(<ProjectOverviewPage/>)}/></Routes></MemoryRouter></QueryClientProvider></StrictMode>);
    expect(screen.getByText("正在读取项目执行上下文…")).toBeTruthy();
    resolve(new Response(JSON.stringify(projectDetail), { status: 200, headers: { "content-type": "application/json" } }));
    expect(await screen.findByRole("heading", { name: "项目一" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "重置项目工作区" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("高风险操作先展示影响说明，取消后不发送命令", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo) => new Response(JSON.stringify(String(input) === "/v1/projects/pj1" ? projectDetail : []), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/pj1/overview"]}><Routes><Route path="/projects/:projectId/overview" element={withIdentity(<ProjectOverviewPage/>)}/></Routes></MemoryRouter></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "归档项目" }));
    expect(screen.getByRole("alertdialog", { name: "归档“项目一”" })).toBeTruthy();
    expect(screen.getByText(/当前版本不支持恢复/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ method: "POST" }));
  });

  it("通过项目治理 Interface 授权固定流水线版本", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url === "/v1/projects/pj1/pipeline-bindings" && init?.method === "PUT") return new Response(JSON.stringify({ project_id: "pj1", pipeline_id: "release", pipeline_revision: 2, enabled: true, is_default: true, version: 1 }), { status: 200, headers: { "content-type": "application/json" } });
      if (url === "/v1/projects/pj1") return new Response(JSON.stringify(projectDetail), { status: 200, headers: { "content-type": "application/json" } });
      if (url === "/v1/pipelines") return new Response(JSON.stringify([{ id: "release", name: "发布流水线", description: "", draft_id: "draft-1", active_revision: 2, created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" }]), { status: 200, headers: { "content-type": "application/json" } });
      if (url === "/v1/agent-deployments") return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
      return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/pj1/overview"]}><Routes><Route path="/projects/:projectId/overview" element={withIdentity(<ProjectOverviewPage/>)}/></Routes></MemoryRouter></QueryClientProvider>);

    await userEvent.click(await screen.findByRole("button", { name: /授\s*权/ }));
    expect(fetchMock).toHaveBeenCalledWith("/v1/projects/pj1/pipeline-bindings", expect.objectContaining({ method: "PUT", body: JSON.stringify({ pipeline_revision_id: "release:2", enabled: true, is_default: true }) }));
  });
});
