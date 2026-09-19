// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IdentityProvider } from "../../features/identity/AuthGate";
import { AppShell } from "./AppShell";

const user = { id: "user-1", username: "evaluator", display_name: "评测管理员", role: "administrator", enabled: true, authorization_version: 1, version: 1, created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T00:00:00Z" } as const;

afterEach(() => vi.unstubAllGlobals());

describe("移动端控制面导航", () => {
  it("通过单一入口提供九个工作区、项目切换和退出", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { id: "project-1", slug: "project-1", name: "项目一", description: "", lifecycle_status: "active", version: 1, created_by: "admin", created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T00:00:00Z" },
      { id: "project-2", slug: "project-2", name: "项目二", description: "", lifecycle_status: "active", version: 1, created_by: "admin", created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T00:00:00Z" },
    ]), { status: 200, headers: { "content-type": "application/json" } })));
    const logout = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IdentityProvider user={user} logout={logout}>
          <MemoryRouter initialEntries={["/projects/project-1/deliveries"]}>
            <Routes><Route element={<AppShell/>}><Route path="projects/:projectId/deliveries" element={<div>交付内容</div>}/></Route></Routes>
          </MemoryRouter>
        </IdentityProvider>
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "打开导航与账户" }));
    const navigation = screen.getByRole("dialog", { name: "导航与账户" });
    expect(navigation.querySelectorAll("a")).toHaveLength(9);
    expect(within(navigation).getByRole("link", { name: "交付工作台" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByLabelText("移动端当前项目")).toBeTruthy();
    expect(within(navigation).getByText("评测管理员")).toBeTruthy();
    await userEvent.click(within(navigation).getByRole("button", { name: "退出登录" }));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("按 Escape 关闭抽屉后把焦点还给导航入口", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { id: "project-1", slug: "project-1", name: "项目一", description: "", lifecycle_status: "active", version: 1, created_by: "admin", created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T00:00:00Z" },
    ]), { status: 200, headers: { "content-type": "application/json" } })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IdentityProvider user={user}>
          <MemoryRouter initialEntries={["/projects/project-1/deliveries"]}>
            <Routes><Route element={<AppShell/>}><Route path="projects/:projectId/deliveries" element={<div>交付内容</div>}/></Route></Routes>
          </MemoryRouter>
        </IdentityProvider>
      </QueryClientProvider>,
    );

    const trigger = screen.getByRole("button", { name: "打开导航与账户" });
    await userEvent.click(trigger);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "导航与账户" })).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });
});
