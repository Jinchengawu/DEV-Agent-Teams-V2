import { describe, expect, it } from "vitest";
import {
  addLoopBodyNode,
  canConnectGraph,
  changeCapability,
  changeStageMode,
  createDependencyEdge,
  createGraphNode,
  parseDefinition,
  removeLoopBodyNode,
  isDeploymentSelectable,
  setEdgeCondition,
  graphViewportIdentity,
  createFlowNode,
} from "./OrchestrationPage";

describe("DAG 与 LOOP 流水线编辑控制器", () => {
  it("创建 Stage、Gate 和有界 LOOP 节点", () => {
    const role = createGraphNode("role", []);
    const code = createGraphNode("code", [role]);
    const gate = createGraphNode("gate", [role, code]);
    const loop = createGraphNode("loop", [role, code, gate]);
    expect(role).toMatchObject({ id: "stage-1", kind: "stage" });
    expect(code).toMatchObject({ id: "code-1", bindings: { developer: "codex-backend" } });
    expect(gate).toMatchObject({ id: "gate-1", kind: "approval_gate", subject_kind: "delivery-plan" });
    expect(loop).toMatchObject({ id: "loop-1", kind: "loop", policy: { exit_condition: "machine-tests-passed", max_iterations: 3, on_exhausted: "fail" } });
  });
  it("拒绝自环、重复边和会形成 DAG 环路的回边", () => {
    const nodes = [createGraphNode("role", []), createGraphNode("code", [])];
    expect(canConnectGraph(nodes, [], "stage-1", "stage-1")).toBe(false);
    expect(canConnectGraph(nodes, [{ source: "stage-1", target: "code-1" }], "stage-1", "code-1")).toBe(false);
    expect(canConnectGraph(nodes, [{ source: "stage-1", target: "code-1" }], "code-1", "stage-1")).toBe(false);
  });
  it("保留图的显式节点和语义边", () => {
    const definition = parseDefinition({ id: "release", version: "4.0.0", nodes: [{ id: "plan", kind: "stage", workflow_mode: "agentscope.role-turn", bindings: { actor: "hermes-pm" } }, { id: "approve", kind: "approval_gate", subject_kind: "artifact" }], edges: [{ source: "plan", target: "approve", condition: "ready" }] });
    expect(definition.nodes).toHaveLength(2);
    expect(definition.edges).toEqual([{ source: "plan", target: "approve", condition: "ready" }]);
  });
  it("切换 Stage 模式时重建 Capability 绑定槽位", () => {
    const node = createGraphNode("role", []);
    if (node.kind !== "stage") throw new Error("测试需要 Stage");
    const code = changeStageMode(node, "code-delivery");
    const admin = changeCapability(changeStageMode(code, "agentscope.role-turn"), "hermes-project-admin");
    expect(code.bindings).toEqual({ developer: "codex-backend" });
    expect(admin.bindings).toEqual({ actor: "hermes-project-admin" });
  });
  it("编辑条件边并把条件写回语义数据", () => {
    const edges = [{ id: "edge-plan-code", source: "plan", target: "code", data: {} }];
    expect(setEdgeCondition(edges, "edge-plan-code", "approved")[0]?.data).toEqual({
      condition: "approved",
    });
    expect(setEdgeCondition(edges, "edge-plan-code", "   ")[0]?.data).toEqual({
      condition: undefined,
    });
  });
  it("通过无障碍依赖编辑器创建带条件的边", () => {
    expect(createDependencyEdge("plan", "code", "approved")).toMatchObject({
      id: "edge-plan-code",
      source: "plan",
      target: "code",
      data: { condition: "approved" },
      label: "approved",
    });
  });
  it("在 LOOP 内部增加节点并在删除时清理关联边", () => {
    const created = createGraphNode("loop", []);
    if (created.kind !== "loop") throw new Error("测试需要 LOOP");
    const withGate = addLoopBodyNode(created, "gate");
    const gate = withGate.nodes.find((node) => node.kind === "approval_gate");
    if (!gate) throw new Error("应创建内部 Gate");
    const connected = {
      ...withGate,
      edges: [{ source: withGate.nodes[0]!.id, target: gate.id, condition: "retry" }],
    };
    const removed = removeLoopBodyNode(connected, gate.id);
    expect(withGate.nodes).toHaveLength(2);
    expect(removed.nodes).toHaveLength(1);
    expect(removed.edges).toEqual([]);
  });
  it("只有 Profile 与 Provider 同时声明 Capability 时才允许分配", () => {
    const deployment = {
      enabled: true,
      qualification_status: "qualified",
      provider_id: "codex-cli-provider",
      capability_requirements: [{ id: "frontend.implementation", version: ">=1,<2" }],
    } as Parameters<typeof isDeploymentSelectable>[0];
    const providers = new Set(["codex-cli-provider"]);
    expect(isDeploymentSelectable(deployment, providers, "frontend.implementation")).toBe(true);
    expect(isDeploymentSelectable(deployment, providers, "hermes-pm")).toBe(false);
  });
  it("相同节点数的不同草稿仍产生不同视口恢复标识", () => {
    const nodes = [
      { id: "plan", position: { x: 20, y: 40 } },
      { id: "code", position: { x: 260, y: 40 } },
    ];
    expect(graphViewportIdentity("draft-a", 2, nodes)).not.toBe(
      graphViewportIdentity("draft-b", 2, nodes),
    );
    expect(graphViewportIdentity("draft-a", 2, nodes)).not.toBe(
      graphViewportIdentity("draft-a", 2, [
        nodes[0]!,
        { id: "code", position: { x: 2600, y: 40 } },
      ]),
    );
  });
  it("为画布节点提供稳定初始尺寸，避免首次测量前被隐藏", () => {
    const node = createGraphNode("role", []);
    expect(createFlowNode(node, { x: 20, y: 40 })).toMatchObject({
      id: "stage-1",
      width: 170,
      height: 68,
    });
  });
});
