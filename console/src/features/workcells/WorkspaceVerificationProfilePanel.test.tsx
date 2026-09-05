// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceVerificationProfilePanel } from "./WorkspaceVerificationProfilePanel";
import type { WorkspaceBinding } from "./api";

const profile = { id: "python-unittest-v1", revision: 1, name: "Python unittest（须发现测试）", commands: [["python", "-m", "unittest"]], timeout_seconds: 300, environment: { CI: "1" }, result_contract: "python-unittest-count-v1", tool_names: ["python"] };
const workspace = { id: "workspace-1", project_id: "project-1", kind: "git_repository_v1", adapter_type: "external-git", repository_uri: "https://github.com/example/backend.git", status: "ready", version: 7, verification_profile_id: "python-unittest-v1", verification_profile: null } as WorkspaceBinding;
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("工作区机器验证方案", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("展示产品命令并以当前Workspace版本提交工具链资格请求", async () => {
    const writes: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/v1/verification-profiles?")) return response([profile]);
      writes.push({ url, body: JSON.parse(String(init?.body)) });
      return response(workspace);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkspaceVerificationProfilePanel projectId="project-1" workspace={workspace}/></QueryClientProvider>);
    expect(await screen.findByText("python -m unittest")).toBeTruthy();
    expect(screen.getByText(/不代表仓库测试已通过/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "验证工具链" }));
    await waitFor(() => expect(writes).toEqual([{ url: "/v1/workspace-bindings/workspace-1/verification-profile/qualify", body: { expected_version: 7 } }]));
  });

  it("没有已保存方案时不能发起工具链资格请求", async () => {
    vi.stubGlobal("fetch", async () => response([profile]));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkspaceVerificationProfilePanel projectId="project-1" workspace={{ ...workspace, verification_profile_id: null }}/></QueryClientProvider>);
    expect((screen.getByRole("button", { name: "验证工具链" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/历史交付仍可查看/)).toBeTruthy();
  });
});
