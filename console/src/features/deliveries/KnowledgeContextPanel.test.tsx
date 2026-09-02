// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { KnowledgeContextPanel } from "./KnowledgeContextPanel";

const now = "2026-09-02T00:00:00Z";
const hash = "b".repeat(64);

afterEach(() => vi.unstubAllGlobals());

test("交付运行室展示冻结知识、不可用回执和 Workcell 引用投影", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({
    delivery_id: "delivery-1",
    delivery_status: "executing",
    preparation_run: {
      id: "prep-1",
      delivery_id: "delivery-1",
      input_sha256: hash,
      knowledge_binding_hash: hash,
      preparation_input: { schema: "knowledge-preparation-input-v1" },
      status: "succeeded",
      attempt_count: 1,
      authorization_epoch_hash: hash,
      created_at: now,
      updated_at: now,
    },
    contexts: [{
      stage_path: "design-repair/design",
      artifact_reference: { uri: `artifact://sha256/${hash}`, sha256: hash, media_type: "application/vnd.agent-team-os.knowledge-context+json", size_bytes: 420 },
      citation_ids: ["CIT-1"],
      authorization_epoch_hash: hash,
      trust_class: "external-collaborative",
    }],
    unavailable: [{
      stage_path: "backend-repair/backend",
      receipt_reference: { uri: `artifact://sha256/${hash}`, sha256: hash, media_type: "application/vnd.agent-team-os.knowledge-context-unavailable+json", size_bytes: 80 },
      error_code: "KNOWLEDGE_SOURCE_REVOKED",
    }],
    citations: [{ citation_id: "CIT-1", stage_paths: ["design-repair/design"], workcell_run_ids: ["workcell-1"] }],
  }), { status: 200, headers: { "content-type": "application/json" } }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KnowledgeContextPanel projectId="project-1" deliveryId="delivery-1"/></QueryClientProvider>);

  expect(await screen.findByRole("heading", { name: "Delivery Knowledge Context" })).toBeTruthy();
  expect((await screen.findAllByText("design-repair/design")).length).toBeGreaterThan(0);
  expect(await screen.findByText("KNOWLEDGE_SOURCE_REVOKED")).toBeTruthy();
  expect(await screen.findByText("workcell-1")).toBeTruthy();
});
