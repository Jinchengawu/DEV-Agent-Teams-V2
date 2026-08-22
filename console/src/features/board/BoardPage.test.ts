import { describe, expect, it } from "vitest";
import { resolveDropCommand } from "./BoardPage";

const item = {
  id: "delivery-1",
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
