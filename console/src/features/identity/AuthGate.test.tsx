// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AuthGate } from "./AuthGate";

afterEach(() => vi.restoreAllMocks());

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
