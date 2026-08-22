import { describe, expect, it } from "vitest";
import { orderedStepIds } from "./contracts";
import { commandLabel, httpErrorLabel, statusLabel } from "./i18n";

describe("published Journey contract", () => {
  it("uses ACWM step order rather than canvas coordinates", () => {
    const revision = {
      journey_id: "backend-delivery",
      revision: 2,
      definition: {
        steps: [
          { kind: "stage", id: "requirements" },
          { kind: "approval_gate", id: "approve-plan" },
          { kind: "stage", id: "delivery" },
        ],
      },
      binding_snapshot: {},
      fingerprint: "a".repeat(64),
      published_at: "2026-08-22T00:00:00Z",
      layout: { delivery: { x: 0 }, requirements: { x: 999 } },
    };

    expect(orderedStepIds(revision)).toEqual([
      "requirements",
      "approve-plan",
      "delivery",
    ]);
  });

  it("将接口失败转换为可操作的中文提示", () => {
    expect(httpErrorLabel(409)).toContain("刷新后重试");
    expect(httpErrorLabel(503)).toContain("运行依赖");
  });
});

describe("中文界面词汇", () => {
  it("将机器状态和看板命令显示为中文", () => {
    expect(statusLabel("awaiting_candidate_decision")).toBe("等待候选审批");
    expect(statusLabel("completed")).toBe("已完成");
    expect(commandLabel("approve-plan")).toBe("批准计划");
    expect(commandLabel("accept-candidate")).toBe("接受候选");
  });
});
