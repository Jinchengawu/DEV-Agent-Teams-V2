import { describe, expect, it } from "vitest";
import { boardColumns, filterWorkItems, resolveDropCommand, sliceBoardLaneItems } from "./BoardPage";

const item = {
  id: "delivery-1",
  project_id: "pj1",
  delivery_id: "delivery-1",
  title: "健康检查",
  column: "plan-approval" as const,
  acceptance_ids: ["AC-001"],
  available_commands: ["approve-plan", "reject-plan"] as Array<"approve-plan" | "reject-plan">,
  version: 3,
};

describe("看板拖放命令控制器", () => {
  it("只允许把计划审批映射为批准命令", () => {
    expect(resolveDropCommand(item, "executing")).toBe("approve-plan");
    expect(resolveDropCommand(item, "completed")).toBeUndefined();
  });
});

describe("看板项目任务检索", () => {
  it("按状态列、交付 ID 和验收 ID 过滤真实投影", () => {
    const items = [
      { id: "w1", delivery_id: "delivery-alpha", title: "实现健康检查", acceptance_ids: ["AC-HEALTH-001"], column: "plan-approval", available_commands: ["approve-plan"], version: 1 },
      { id: "w2", delivery_id: "delivery-beta", title: "修复登录", acceptance_ids: ["AC-AUTH-001"], column: "executing", available_commands: ["cancel"], version: 1 },
    ] as Parameters<typeof filterWorkItems>[0];
    expect(filterWorkItems(items, "health", "all").map((item) => item.id)).toEqual(["w1"]);
    expect(filterWorkItems(items, "delivery-beta", "executing").map((item) => item.id)).toEqual(["w2"]);
    expect(filterWorkItems(items, "", "plan-approval").map((item) => item.id)).toEqual(["w1"]);
  });
});

describe("看板发布恢复列", () => {
  it("将部分 Apply 与普通执行、失败和取消分开展示", () => {
    expect(boardColumns).toContainEqual({
      id: "needs-attention",
      label: "需要人工恢复",
      note: "部分仓已推进，只能 Resume forward",
    });
  });
});

describe("高密度看板列", () => {
  it("默认只展示一屏任务，并明确告知剩余数量", () => {
    const items = Array.from({ length: 25 }, (_, index) => ({
      ...item,
      id: `delivery-${index}`,
      delivery_id: `delivery-${index}`,
    }));

    expect(sliceBoardLaneItems(items, false)).toMatchObject({
      visible: items.slice(0, 12),
      remaining: 13,
    });
    expect(sliceBoardLaneItems(items, true)).toMatchObject({
      visible: items,
      remaining: 0,
    });
  });
});
