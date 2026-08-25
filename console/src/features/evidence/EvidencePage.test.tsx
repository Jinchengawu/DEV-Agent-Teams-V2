// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidencePage } from "./EvidencePage";

const sha256 = "a".repeat(64);
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/pj1/evidence?delivery_id=delivery-1"]}><Routes><Route path="/projects/:projectId/evidence" element={<EvidencePage/>}/></Routes></MemoryRouter></QueryClientProvider>);
}

describe("项目证据工作台", () => {
  it("按深链筛选真实证据并展示不可变重新验证历史", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url === "/v1/evidence?project_id=pj1") return response([{ id: "evidence-1", project_id: "pj1", delivery_id: "delivery-1", kind: "journey", source_kind: "delivery", source_id: "delivery-1", producer_identity: "codex-cli", content_sha256: sha256, status: "verified", payload: { acceptance_id: "AC-001" } }]);
      if (url === "/v1/evidence/evidence-1/verifications") return response([{ id: "verification-1", evidence_id: "evidence-1", status: "verified", error: null, verified_at: "2026-08-24T10:00:00Z" }]);
      return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
    }));
    renderPage();

    expect(await screen.findByDisplayValue("delivery-1")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /delivery-1.*codex-cli/i }));
    expect(await screen.findByText("哈希与当前不可变内容一致")).toBeTruthy();
    expect(screen.getByText(/AC-001/)).toBeTruthy();
  });

  it("剪贴板被拒绝时给出可执行的中文修复动作", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url === "/v1/evidence?project_id=pj1") return response([{ id: "evidence-1", project_id: "pj1", delivery_id: "delivery-1", kind: "journey", source_kind: "delivery", source_id: "delivery-1", producer_identity: "codex-cli", content_sha256: sha256, status: "verified", payload: {} }]);
      if (url === "/v1/evidence/evidence-1/verifications") return response([]);
      return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
    }));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: /delivery-1.*codex-cli/i }));
    await userEvent.click(screen.getByRole("button", { name: "复制内容哈希" }));
    expect(await screen.findByText(/浏览器未授权读取剪贴板/)).toBeTruthy();
  });
});
