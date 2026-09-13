// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useProjectWorkcells, useVerifyWorkspaceBinding } from "./api";

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json" },
});

function VerificationHarness() {
  const topology = useProjectWorkcells("project-1");
  const verify = useVerifyWorkspaceBinding("project-1");
  const workspace = topology.data?.workspace_bindings[0];
  if (!workspace) return <span>加载中</span>;
  return <button onClick={() => verify.mutate({ workspaceId: workspace.id, expectedVersion: workspace.version })}>
    验证版本 {workspace.version}
  </button>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Workcell 仓库验证", () => {
  it("验证失败后刷新 Workspace 版本，使用户可以直接重试", async () => {
    let topologyReads = 0;
    vi.stubGlobal("fetch", async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/v1/projects/project-1/workcells" && method === "GET") {
        topologyReads += 1;
        return response({
          team_binding: { status: "provisioning", version: 1 },
          team_revision: { workcells: [] },
          workcell_bindings: [],
          workspace_bindings: [{ id: "workspace-1", version: topologyReads, status: "failed" }],
        });
      }
      if (url === "/v1/workspace-bindings/workspace-1/verify" && method === "POST") {
        return response({ code: "WORKSPACE_VERIFICATION_FAILED", title: "验证失败", detail: "仓库暂不可用" }, 409);
      }
      return response({}, 404);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><VerificationHarness/></QueryClientProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "验证版本 1" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "验证版本 2" })).toBeTruthy());
    expect(topologyReads).toBe(2);
  });
});
