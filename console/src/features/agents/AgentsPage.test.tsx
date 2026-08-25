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
const providers = [{ id: "codex-cli-provider", revision: "1", fingerprint: "a".repeat(64), runtime_types: ["codex-cli"], capabilities: [{ id: "codex-backend", version: "1.0.0" }], workflow_modes: ["code-delivery"], required_features: [], input_contracts: [], output_contracts: [], permission_requirements: [] }];
const adapters = [{ id: "codex-cli", version: "1", runtime_type: "codex-cli", features: ["workspace.cwd_binding"], available: true, features_source: "installed-acwm-adapter-manifest", error_code: null }];
const deployments = [{ id: "backend-codex", name: "后端 Codex 部署", profile_id: "backend-engineer", profile_revision: 1, profile_sha256: "b".repeat(64), capability_requirements: [{ id: "codex-backend", version: ">=1,<2" }], instance_id: "codex-1", instance_version: 2, adapter_id: "codex-cli", adapter_version: "1", provider_id: "codex-cli-provider", provider_revision: "1", provider_fingerprint: "a".repeat(64), isolation_mode: "shared", policy_snapshot: {}, qualification_status: "qualified", qualification_errors: [], enabled: true, version: 3, created_by: "admin", created_at: now, updated_at: now }];

type FetchCall = { url: string; method: string; body?: string };
const calls: FetchCall[] = [];
let profileItems: unknown[] = [];
let deploymentItems: Array<Record<string, unknown>> = deployments;
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
  deploymentItems = deployments;
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
    if (call.url === "/v1/provider-manifests" && call.method === "GET") return response(providers);
    if (call.url === "/v1/runtime-adapters" && call.method === "GET") return response(adapters);
    if (call.url === "/v1/agent-deployments" && call.method === "GET") return response(deploymentItems);
    if (call.url === "/v1/agent-deployments/backend-codex" && call.method === "PATCH") return response({ ...deploymentItems[0], instance_version: 2, qualification_status: "unknown", qualification_errors: [], enabled: false, version: 4 });
    if (call.url === "/v1/agent-deployments/backend-codex/qualify" && call.method === "POST") return response({ ...deploymentItems[0], instance_version: 2, qualification_status: "qualified", qualification_errors: [], version: 5 });
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><AgentsPage/></QueryClientProvider>);
}

describe("智能体角色、部署与运行实例", () => {
  test("读取真实实例和 Deployment 快照", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Agent 部署" }));
    await screen.findByRole("heading", { name: "后端 Codex 部署" });
    expect(screen.getByRole("tab", { name: "Agent 部署" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("heading", { name: "后端 Codex 部署" })).toBeTruthy();
    expect(calls.some((call) => call.url === "/v1/agent-deployments")).toBe(true);
    await userEvent.click(screen.getByRole("tab", { name: "运行实例" }));
    await screen.findByRole("heading", { name: "Codex 主执行器" });
  });

  test("资格检查携带 Deployment 当前 CAS 版本", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Agent 部署" }));
    const card = (await screen.findByRole("heading", { name: "后端 Codex 部署" })).closest("article");
    expect(card).not.toBeNull();
    const controls = within(card!);
    await userEvent.click(controls.getByRole("button", { name: "资格检查" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/agent-deployments/backend-codex/qualify" && call.method === "POST")).toBe(true));
    const checked = calls.find((call) => call.url === "/v1/agent-deployments/backend-codex/qualify" && call.method === "POST");
    expect(JSON.parse(checked?.body ?? "{}")).toEqual({ expected_version: 3 });
  });

  test("过期实例快照可以刷新并自动重新执行资格检查", async () => {
    deploymentItems = [{ ...deployments[0], instance_version: 1, qualification_status: "failed", qualification_errors: ["RUNTIME_INSTANCE_VERSION_STALE"], enabled: false, version: 3 }];
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Agent 部署" }));
    await userEvent.click(await screen.findByRole("button", { name: "刷新快照并重新检查" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/v1/agent-deployments/backend-codex/qualify" && call.method === "POST")).toBe(true));
    const patch = calls.find((call) => call.url === "/v1/agent-deployments/backend-codex" && call.method === "PATCH");
    const qualify = calls.find((call) => call.url === "/v1/agent-deployments/backend-codex/qualify" && call.method === "POST");
    expect(JSON.parse(patch?.body ?? "{}")).toEqual({ expected_version: 3, instance_id: "codex-1" });
    expect(JSON.parse(qualify?.body ?? "{}")).toEqual({ expected_version: 4 });
  });

  test("Hermes 实例必须显式填写连接端点，不能使用硬编码地址", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "运行实例" }));
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

  test("Provider 与 Adapter 工作区展示可信安装来源", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Provider 能力" }));
    await screen.findByRole("heading", { name: "codex-cli-provider" });
    expect(screen.getByText(/codex-backend@1.0.0/)).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "Runtime Adapter" }));
    await screen.findByRole("heading", { name: "codex-cli" });
    expect(screen.getByText("installed-acwm-adapter-manifest")).toBeTruthy();
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
