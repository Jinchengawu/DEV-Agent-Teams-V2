import { describe, expect, it } from "vitest";
import { createGraphNode } from "./OrchestrationPage";
import { appendWorkspaceNode, createGraphEditingWorkspace } from "./GraphEditingWorkspace";
import { commitGraphEditingWorkspace, commitTransientGraphEditingWorkspace, createGraphEditingHistory, redoGraphEditingWorkspace, replaceGraphEditingWorkspace, undoGraphEditingWorkspace } from "./GraphEditingHistory";

describe("GraphEditingHistory 命令历史 Interface", () => {
  it("撤销和重做恢复完整语义工作区", () => {
    const initial = createGraphEditingWorkspace([], [], undefined, {});
    const node = createGraphNode("role", []);
    const changed = appendWorkspaceNode(initial, node, { x: 10, y: 20 });
    const committed = commitGraphEditingWorkspace(createGraphEditingHistory(initial), changed);
    expect(undoGraphEditingWorkspace(committed).present.nodes).toEqual([]);
    expect(redoGraphEditingWorkspace(undoGraphEditingWorkspace(committed)).present).toEqual(changed);
  });

  it("拖动过程只在结束时形成一个历史步骤", () => {
    const initial = createGraphEditingWorkspace([createGraphNode("role", [])], [], undefined, {});
    const history = createGraphEditingHistory(initial);
    const moving = { ...initial, layout: { ...initial.layout, [initial.nodes[0].id]: { x: 50, y: 60 } } };
    const moved = { ...moving, layout: { ...moving.layout, [initial.nodes[0].id]: { x: 80, y: 90 } } };
    const transient = replaceGraphEditingWorkspace(replaceGraphEditingWorkspace(history, moving), moved);
    const committed = commitTransientGraphEditingWorkspace(transient, initial);
    expect(committed.past).toHaveLength(1);
    expect(undoGraphEditingWorkspace(committed).present.layout).toEqual(initial.layout);
  });
});
