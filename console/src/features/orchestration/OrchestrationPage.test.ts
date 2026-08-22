import { describe, expect, it } from "vitest";
import { changeCapability, changeStageMode, createStep } from "./OrchestrationPage";

describe("线性 Journey 编辑控制器", () => {
  it("创建稳定且不重复的角色、代码和审批节点", () => {
    const role = createStep("role", []);
    const secondRole = createStep("role", [role]);
    const code = createStep("code", [role, secondRole]);
    const gate = createStep("gate", [role, secondRole, code]);

    expect(role).toMatchObject({ id: "role-1", kind: "stage", bindings: { actor: "hermes-pm" } });
    expect(secondRole.id).toBe("role-2");
    expect(code).toMatchObject({ id: "delivery-1", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" } });
    expect(gate).toMatchObject({ id: "approval-1", kind: "approval_gate", subject_kind: "delivery-plan" });
  });

  it("切换执行模式时重建正确的 ACWM 绑定槽位", () => {
    const role = createStep("role", []);
    if (role.kind !== "stage") throw new Error("测试需要角色阶段");
    const code = changeStageMode(role, "code-delivery");
    const admin = changeCapability(changeStageMode(code, "agentscope.role-turn"), "hermes-project-admin");

    expect(code.bindings).toEqual({ developer: "codex-backend" });
    expect(code.output_validator).toBe("backend-candidate-v1");
    expect(admin.bindings).toEqual({ actor: "hermes-project-admin" });
    expect(admin.output_validator).toBeUndefined();
  });
});
