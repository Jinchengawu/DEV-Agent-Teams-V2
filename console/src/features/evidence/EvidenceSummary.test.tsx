// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EvidenceRecord } from "../../shared/api/client";
import { EvidenceSummary } from "./EvidenceSummary";

describe("证据账本摘要", () => {
  it("按已验证、无效、不可用混合统计真实状态", () => {
    const records: EvidenceRecord[] = [
      { id: "e-1", project_id: "pj1", delivery_id: "d-1", kind: "journey", source_kind: "backend", source_id: "s-1", producer_identity: "a", status: "verified", content_sha256: "sha", verified_at: null, payload: {}, },
      { id: "e-2", project_id: "pj1", delivery_id: "d-1", kind: "journey", source_kind: "backend", source_id: "s-2", producer_identity: "a", status: "invalid", content_sha256: "sha", verified_at: null, payload: {}, },
      { id: "e-3", project_id: "pj1", delivery_id: "d-1", kind: "journey", source_kind: "backend", source_id: "s-3", producer_identity: "a", status: "unavailable", content_sha256: "sha", verified_at: null, payload: {}, },
      { id: "e-4", project_id: "pj1", delivery_id: "d-1", kind: "journey", source_kind: "backend", source_id: "s-4", producer_identity: "a", status: "verified", content_sha256: "sha", verified_at: null, payload: {}, },
    ];

    const { container } = render(<EvidenceSummary records={records} />);

    expect(container.querySelector('[data-stat="total"]')?.textContent).toBe("4");
    expect(container.querySelector('[data-stat="verified"]')?.textContent).toBe("2");
    expect(container.querySelector('[data-stat="invalid"]')?.textContent).toBe("1");
    expect(container.querySelector('[data-stat="unavailable"]')?.textContent).toBe("1");
    expect(container.querySelector('[data-stat="verified"]')?.className).toContain("summary-value--verified");
    expect(container.querySelector('[data-stat="invalid"]')?.className).toContain("summary-value--invalid");
    expect(container.querySelector('[data-stat="unavailable"]')?.className).toContain("summary-value--neutral");
  });

  it("空账本展示四项零值", () => {
    const { container } = render(<EvidenceSummary records={[]} />);

    expect(container.querySelector('[data-stat="total"]')?.textContent).toBe("0");
    expect(container.querySelector('[data-stat="verified"]')?.textContent).toBe("0");
    expect(container.querySelector('[data-stat="invalid"]')?.textContent).toBe("0");
    expect(container.querySelector('[data-stat="unavailable"]')?.textContent).toBe("0");
  });
});
