// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

const now = "2026-08-23T00:00:00Z";
const settings = {
  version: 1,
  planning_timeout_seconds: 120,
  execution_timeout_seconds: 180,
  verification_timeout_seconds: 60,
  evidence_retention_days: 7,
  language: "zh-CN",
  allowed_paths: ["src/**", "tests/**"],
  verification_commands: ["python -m unittest discover -s tests -v"],
  updated_at: now,
};
const deterministic = {
  kind: "deterministic", status: "passed", fail: 0, warn: 0, skipped: 0, created_at: now,
  dev_revision: "a".repeat(40), acwm_revision: "b".repeat(40), planning_identity: "deterministic-test",
  execution_identity: "deterministic-model-boundary", candidate_revision: "c".repeat(40), diff_sha256: "d".repeat(64),
  verification_exit_code: 0, evidence_sha256: "e".repeat(64), browser_e2e: true, browser_restart_recovery: true,
  browser_multi_pipeline_e2e: true, browser_verified_evidence_count: 7, browser_candidate_matches_main: true, error: null,
};
const live = {
  ...deterministic, kind: "live", planning_identity: "codex-simulated-hermes", execution_identity: "codex-cli",
  browser_e2e: false, browser_restart_recovery: false, browser_multi_pipeline_e2e: false,
  browser_verified_evidence_count: 0, browser_candidate_matches_main: false,
};

const calls: string[] = [];
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", async (input: RequestInfo) => {
    const url = String(input);
    calls.push(url);
    if (url === "/v1/settings") return response(settings);
    if (url === "/v1/release-gates/latest") return response({
      deterministic,
      live: { ...live, dev_revision: "f".repeat(40) },
      combined: { status: "failed", code: "RELEASE_GATE_REVISION_MISMATCH", reason: "确定性门禁与真实门禁不是同一代码和 ACWM Revision。" },
    });
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><SettingsPage/></QueryClientProvider>);
}

describe("设置页发布双门禁", () => {
  test("Revision 不一致时明确禁止发布并展示两份身份", async () => {
    renderPage();

    await screen.findByRole("heading", { name: "禁止发布" });
    expect(screen.getByText("RELEASE_GATE_REVISION_MISMATCH")).toBeTruthy();
    expect(screen.getByText("deterministic-model-boundary")).toBeTruthy();
    expect(screen.getByText("codex-cli")).toBeTruthy();
    expect(screen.getByText("浏览器闭环 已执行 · 进程重启恢复 已验证")).toBeTruthy();
    expect(screen.getByText("多流水线闭环 已验证 · 已验证证据 7 条 · Main 精确等于 Candidate")).toBeTruthy();
  });

  test("刷新报告按钮重新请求真实只读接口", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "禁止发布" });
    await userEvent.click(screen.getByRole("button", { name: "刷新报告" }));

    await waitFor(() => expect(calls.filter((url) => url === "/v1/release-gates/latest")).toHaveLength(2));
  });
});
