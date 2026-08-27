// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { AuthGate } from "./AuthGate";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("首次运行展示真实管理员初始化表单", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ bootstrap_required: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <AuthGate><div>受保护的控制台</div></AuthGate>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(view.container.textContent).toContain("初始化管理员"));
  expect(view.container.textContent).toContain("密码仅以 scrypt 哈希保存");
  expect(view.container.textContent).not.toContain("受保护的控制台");
});

test("页面缓存未初始化但系统已有管理员时使用同一凭据登录", async () => {
  const requests: string[] = [];
  const currentUser = {
    id: "user-evaluator",
    username: "evaluator",
    display_name: "评估管理员",
    role: "administrator",
    enabled: true,
    version: 1,
    created_at: "2026-08-28T00:00:00Z",
    updated_at: "2026-08-28T00:00:00Z",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requests.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/v1/auth/bootstrap-status") {
      return new Response(JSON.stringify({ bootstrap_required: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (path === "/v1/auth/bootstrap") {
      return new Response(JSON.stringify({
        code: "IDENTITY_ALREADY_BOOTSTRAPPED",
        title: "系统已完成初始化",
        detail: "管理员账户已存在，不能再次执行首次初始化。",
      }), {
        status: 409,
        headers: { "content-type": "application/problem+json" },
      });
    }
    if (path === "/v1/auth/login" || path === "/v1/auth/session") {
      return new Response(JSON.stringify(currentUser), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`unexpected request: ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthGate><div>受保护的控制台</div></AuthGate>
    </QueryClientProvider>,
  );

  await screen.findByRole("heading", { name: "初始化管理员" });
  const user = userEvent.setup();
  await user.clear(screen.getByLabelText("用户名"));
  await user.type(screen.getByLabelText("用户名"), "evaluator");
  await user.type(screen.getByLabelText("密码"), "race-safe-password-2026");
  await user.click(screen.getByRole("button", { name: "创建并登录" }));

  await screen.findByText("受保护的控制台");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(requests.slice(0, 3)).toEqual([
    "GET /v1/auth/bootstrap-status",
    "POST /v1/auth/bootstrap",
    "POST /v1/auth/login",
  ]);
});
