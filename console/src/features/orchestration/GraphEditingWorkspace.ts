import type { NodeChange } from "@xyflow/react";
import type { GraphNode } from "./OrchestrationPage";

export type GraphPosition = { x: number; y: number };
export type SemanticGraphEdge = { source: string; target: string; condition?: string | null };

export type GraphEditingWorkspace = {
  nodes: GraphNode[];
  edges: SemanticGraphEdge[];
  layout: Record<string, GraphPosition>;
  assignments: Record<string, string>;
};

export function createGraphEditingWorkspace(nodes: GraphNode[], edges: SemanticGraphEdge[], layout: Record<string, unknown> | undefined, assignments: Record<string, string> | undefined): GraphEditingWorkspace {
  const normalizedLayout: Record<string, GraphPosition> = {};
  nodes.forEach((node, index) => {
    const candidate = layout?.[node.id];
    normalizedLayout[node.id] = isPosition(candidate) ? candidate : { x: index * 230, y: index % 2 ? 170 : 40 };
  });
  return {
    nodes,
    edges,
    layout: normalizedLayout,
    assignments: assignments ?? {},
  };
}

export function appendWorkspaceNode(workspace: GraphEditingWorkspace, node: GraphNode, position: GraphPosition): GraphEditingWorkspace {
  return { ...workspace, nodes: [...workspace.nodes, node], layout: { ...workspace.layout, [node.id]: position } };
}

export function updateWorkspaceNode(workspace: GraphEditingWorkspace, node: GraphNode): GraphEditingWorkspace {
  return { ...workspace, nodes: workspace.nodes.map((current) => current.id === node.id ? node : current) };
}

export function applyWorkspaceLayoutChanges(workspace: GraphEditingWorkspace, changes: NodeChange[]): GraphEditingWorkspace {
  const layout = { ...workspace.layout };
  for (const change of changes) {
    if (change.type === "position" && change.position) layout[change.id] = change.position;
  }
  return { ...workspace, layout };
}

export function appendWorkspaceEdge(workspace: GraphEditingWorkspace, edge: SemanticGraphEdge): GraphEditingWorkspace {
  return { ...workspace, edges: [...workspace.edges, edge] };
}

export function updateWorkspaceEdgeCondition(workspace: GraphEditingWorkspace, source: string, target: string, condition: string): GraphEditingWorkspace {
  const normalized = condition.trim() || undefined;
  return { ...workspace, edges: workspace.edges.map((edge) => edge.source === source && edge.target === target ? { ...edge, condition: normalized } : edge) };
}

export function deleteWorkspaceEdge(workspace: GraphEditingWorkspace, source: string, target: string): GraphEditingWorkspace {
  return { ...workspace, edges: workspace.edges.filter((edge) => edge.source !== source || edge.target !== target) };
}

export function assignWorkspaceDeployment(workspace: GraphEditingWorkspace, site: string, deploymentId: string): GraphEditingWorkspace {
  const assignments = { ...workspace.assignments };
  if (deploymentId) assignments[site] = deploymentId;
  else delete assignments[site];
  return { ...workspace, assignments };
}

export function deleteWorkspaceNode(workspace: GraphEditingWorkspace, nodeId: string): GraphEditingWorkspace {
  const layout = { ...workspace.layout };
  delete layout[nodeId];
  return {
    nodes: workspace.nodes.filter((node) => node.id !== nodeId),
    edges: workspace.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    layout,
    assignments: Object.fromEntries(Object.entries(workspace.assignments).filter(([site]) => !site.startsWith(`${nodeId}.`) && !site.startsWith(`${nodeId}/`))),
  };
}

function isPosition(value: unknown): value is GraphPosition {
  return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number";
}
