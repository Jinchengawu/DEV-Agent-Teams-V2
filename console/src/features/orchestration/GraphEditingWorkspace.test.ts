import { describe, expect, it } from "vitest";
import { createGraphNode } from "./OrchestrationPage";
import { appendWorkspaceEdge, applyWorkspaceLayoutChanges, createGraphEditingWorkspace, deleteWorkspaceNode } from "./GraphEditingWorkspace";

describe("GraphEditingWorkspace 单一状态 Interface", () => {
  it("删除节点时原子清理语义边、布局和嵌套 Agent Assignment", () => {
    const stage = createGraphNode("role", []);
    const loop = createGraphNode("loop", [stage]);
    const code = createGraphNode("code", [stage, loop]);
    const initial = createGraphEditingWorkspace(
      [stage, loop, code],
      [{ source: stage.id, target: loop.id }, { source: loop.id, target: code.id }],
      { [stage.id]: { x: 1, y: 2 }, [loop.id]: { x: 3, y: 4 }, [code.id]: { x: 5, y: 6 } },
      { [`${loop.id}.actor`]: "deployment-loop", [`${loop.id}/loop-1-work.developer`]: "deployment-inner", [`${code.id}.developer`]: "deployment-code" },
    );
    const result = deleteWorkspaceNode(initial, loop.id);
    expect(result.nodes.map((node) => node.id)).toEqual([stage.id, code.id]);
    expect(result.edges).toEqual([]);
    expect(result.layout[loop.id]).toBeUndefined();
    expect(result.assignments).toEqual({ [`${code.id}.developer`]: "deployment-code" });
  });

  it("拖动只改变布局，不改变语义节点和依赖边", () => {
    const stage = createGraphNode("role", []);
    const code = createGraphNode("code", [stage]);
    const initial = appendWorkspaceEdge(createGraphEditingWorkspace([stage, code], [], undefined, {}), { source: stage.id, target: code.id });
    const moved = applyWorkspaceLayoutChanges(initial, [{ type: "position", id: stage.id, position: { x: 420, y: 180 }, dragging: false }]);
    expect(moved.layout[stage.id]).toEqual({ x: 420, y: 180 });
    expect(moved.nodes).toEqual(initial.nodes);
    expect(moved.edges).toEqual(initial.edges);
  });
});
