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
let profileItems: unknown[] = [];
const profileSpec = {
  schema_version: "1", id: "frontend-engineer", name: "前端开发工程师", description: "负责前端实现与组件测试", tags: ["development", "frontend"],
  instructions: { template_ref: "prompt://frontend-engineer@1", custom_text: "遵守中文界面、公共 API 和前端架构规范", variables_schema: "schema://agent-prompt-variables@1", examples: [] },
  capabilities: [{ id: "frontend.implementation", version: ">=1,<2" }],
  policies: { tool_policy_ref: "policy://frontend-tools@1", resource_policy_ref: "policy://frontend-resources@1", approval_policy_ref: "policy://candidate-approval@1", memory_policy_ref: "policy://session-isolated@1", delegation_policy_ref: "policy://no-delegation@1" },
  isolation_preference: "shared", extensions: {},
};
const existingProfile = { id: profileSpec.id, name: profileSpec.name, description: profileSpec.description, tags: profileSpec.tags, latest_revision: null, version: 1, created_by: "admin", created_at: now, updated_at: now };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" } });

beforeEach(() => {
  calls.length = 0;
  profileItems = [];
  vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
    const call = { url: String(input), method: (init?.method ?? "GET").toUpperCase(), body: init?.body?.toString() };
    calls.push(call);
    if (call.url === "/v1/agent-instances" && call.method === "GET") return response(instances);
    if (call.url === "/v1/agent-instances" && call.method === "POST") {
      const body = JSON.parse(call.body ?? "{}");
      return response({ ...instances[0], id: "created-instance", ...body }, 201);
    }
    if (call.url === "/v1/agent-profiles" && call.method === "GET") return response(profileItems);
    if (call.url === "/v1/agent-profiles" && call.method === "POST") {
      const spec = JSON.parse(call.body ?? "{}").spec;
      if (profileItems.some((item) => (item as { id: string }).id === spec.id)) return response({ code: "AGENT_PROFILE_EXISTS", title: "智能体角色已存在", detail: `角色 ${spec.id} 已经存在。`, repair: "更换角色 ID，或编辑现有角色草稿。" }, 409);
      return response({
        profile: { id: spec.id, name: spec.name, description: spec.description, tags: spec.tags, latest_revision: null, version: 1, created_by: "admin", created_at: now, updated_at: now },
        draft: { profile_id: spec.id, spec, version: 1, validation_status: "unknown", validation_errors: [], updated_by: "admin", updated_at: now },
      });
    }
    if (call.url === "/v1/agent-profiles/frontend-engineer/draft" && call.method === "GET") return response({ profile_id: profileSpec.id, spec: profileSpec, version: 2, validation_status: "valid", validation_errors: [], updated_by: "admin", updated_at: now });
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

  test("Hermes 实例必须显式填写连接端点，不能使用硬编码地址", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Codex 主执行器" });
    await userEvent.selectOptions(screen.getByLabelText("运行时类型"), "hermes-http");
    expect((screen.getByLabelText("连接端点") as HTMLInputElement).value).toBe("");
    await userEvent.type(screen.getByLabelText("实例名称"), "Hermes PM 01");
    expect((screen.getByRole("button", { name: "注册实例" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("连接端点"), "http://127.0.0.1:9100");
    await userEvent.click(screen.getByRole("button", { name: "注册实例" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/agent-instances" && call.method === "POST")).toBe(true));
    const created = calls.find((call) => call.url === "/v1/agent-instances" && call.method === "POST");
    expect(JSON.parse(created?.body ?? "{}").connection).toEqual({ endpoint: "http://127.0.0.1:9100" });
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

  test("已有角色不会污染新建表单，选择后进入编辑状态", async () => {
    profileItems = [existingProfile];
    renderPage();

    await screen.findByRole("heading", { name: "创建智能体角色" });
    expect((screen.getByLabelText("角色 ID") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("button", { name: "创建角色草稿" }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /前端开发工程师.*frontend-engineer/ }));
    await screen.findByRole("heading", { name: "编辑智能体角色" });
    expect((screen.getByLabelText("角色 ID") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发布不可变 Revision" }) as HTMLButtonElement).disabled).toBe(false);
  });

  test("未保存修改不能被误当作已校验版本发布", async () => {
    profileItems = [existingProfile];
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /前端开发工程师.*frontend-engineer/ }));
    await screen.findByText(/草稿版本 2/);

    await userEvent.clear(screen.getByLabelText("角色名称"));
    await userEvent.type(screen.getByLabelText("角色名称"), "前端开发负责人");

    expect(screen.getByText(/有未保存修改/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存草稿" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "校验当前版本" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发布不可变 Revision" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("重复创建错误在切换到现有角色后清除", async () => {
    profileItems = [existingProfile];
    renderPage();
    await screen.findByRole("heading", { name: "创建智能体角色" });
    await userEvent.type(screen.getByLabelText("角色 ID"), "frontend-engineer");
    await userEvent.type(screen.getByLabelText("角色名称"), "重复角色");
    await userEvent.click(screen.getByRole("button", { name: "创建角色草稿" }));
    await screen.findByText("智能体角色已存在");

    await userEvent.click(screen.getByRole("button", { name: /前端开发工程师.*frontend-engineer/ }));
    await screen.findByRole("heading", { name: "编辑智能体角色" });
    expect(screen.queryByText("智能体角色已存在")).toBeNull();
  });
});
