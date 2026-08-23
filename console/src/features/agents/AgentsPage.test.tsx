// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { AgentsPage } from "./AgentsPage";

const now = "2026-08-23T00:00:00Z";
const instances = [
  {
    id: "codex-1", name: "Codex 主执行器", runtime_type: "codex-cli", connection: { command: "codex" }, credential_ref: null,
    features: ["io.text.final", "workspace.cwd_binding"], enabled: true, version: 2,
    health: { status: "ready", identity: "codex-cli", latency_ms: 3, error_code: null, checked_at: now }, created_at: now, updated_at: now,
  },
  {
    id: "codex-2", name: "Codex 候选执行器", runtime_type: "codex-cli", connection: { command: "codex" }, credential_ref: null,
    features: ["io.text.final", "workspace.cwd_binding"], enabled: true, version: 4,
    health: { status: "ready", identity: "codex-cli", latency_ms: 4, error_code: null, checked_at: now }, created_at: now, updated_at: now,
  },
];
const bindings = [
  { capability_id: "codex-backend", instance_id: "codex-1", instance_version: 2, version: 3, updated_at: now },
];

type FetchCall = { url: string; method: string; body?: string };
const calls: FetchCall[] = [];
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const call = { url: String(input), method: (init?.method ?? "GET").toUpperCase(), body: init?.body?.toString() };
    calls.push(call);
    if (call.url === "/v1/agent-instances") return response(instances);
    if (call.url === "/v1/agent-profiles" && call.method === "GET") return response([]);
    if (call.url === "/v1/agent-profiles" && call.method === "POST") {
      const spec = JSON.parse(call.body ?? "{}").spec;
      return response({
        profile: { id: spec.id, name: spec.name, description: spec.description, tags: spec.tags, latest_revision: null, version: 1, created_by: "admin", created_at: now, updated_at: now },
        draft: { profile_id: spec.id, spec, version: 1, validation_status: "unknown", validation_errors: [], updated_by: "admin", updated_at: now },
      });
    }
    if (call.url === "/v1/capability-bindings" && call.method === "GET") return response(bindings);
    if (call.url === "/v1/capability-bindings/codex-backend" && call.method === "PUT") {
      return response({ ...bindings[0], instance_id: "codex-2", instance_version: 4, version: 4 });
    }
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><AgentsPage/></QueryClientProvider>);
}

describe("智能体实例与能力绑定", () => {
  test("读取真实实例和绑定快照", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Codex 主执行器" });
    expect(screen.getByText("能力绑定")).toBeTruthy();
    expect(calls.some((call) => call.url === "/v1/capability-bindings")).toBe(true);
  });

  test("保存绑定时携带当前 CAS 版本", async () => {
    renderPage();
    const card = (await screen.findByRole("heading", { name: "后端代码交付" })).closest("article");
    expect(card).not.toBeNull();
    const controls = within(card!);
    await userEvent.selectOptions(controls.getByRole("combobox", { name: "为 后端代码交付 选择实例" }), "codex-2");
    await userEvent.click(controls.getByRole("button", { name: "保存能力绑定" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/capability-bindings/codex-backend" && call.method === "PUT")).toBe(true));
    const saved = calls.find((call) => call.url === "/v1/capability-bindings/codex-backend" && call.method === "PUT");
    expect(JSON.parse(saved?.body ?? "{}")).toEqual({ instance_id: "codex-2", expected_version: 3 });
  });

  test("可以从中文表单创建前端开发角色草稿", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "创建智能体角色" });
    await userEvent.clear(screen.getByLabelText("角色 ID"));
    await userEvent.type(screen.getByLabelText("角色 ID"), "frontend-engineer");
    await userEvent.clear(screen.getByLabelText("角色名称"));
    await userEvent.type(screen.getByLabelText("角色名称"), "前端开发工程师");
    await userEvent.click(screen.getByRole("button", { name: "创建角色草稿" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/agent-profiles" && call.method === "POST")).toBe(true));
    const created = calls.find((call) => call.url === "/v1/agent-profiles" && call.method === "POST");
    expect(JSON.parse(created?.body ?? "{}").spec.capabilities[0].id).toBe("frontend.implementation");
  });
});
