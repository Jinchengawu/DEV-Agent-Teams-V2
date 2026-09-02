import type { NodeChange } from "@xyflow/react";
import type {
  ArtifactContract,
  GraphNode,
  StageNode,
} from "./OrchestrationPage";

export type GraphPosition = { x: number; y: number };
export type SemanticGraphEdge = { source: string; target: string; condition?: string | null };

export type KnowledgeContextBindingDraft = {
  stage_path: string;
  acwm_artifact_slot: "knowledge-context-v1";
  acwm_artifact_contract_version: string;
  acwm_artifact_contract_sha256: string;
  retrieval_policy_revision_id: string;
  required: boolean;
  max_context_bytes: number;
};

export const KNOWLEDGE_CONTEXT_ARTIFACT_CONTRACT: ArtifactContract = {
  id: "knowledge-context-v1",
  version: "1.0.0",
  schema_uri: null,
  modalities: ["structured", "text"],
  integrity: "sha256-required",
  provenance: "required",
  verification: "schema",
};

// This is the content address of the ACWM-owned contract above. Pipeline
// validation fails closed if the pinned ACWM Revision resolves a different hash.
export const KNOWLEDGE_CONTEXT_ARTIFACT_CONTRACT_SHA256 =
  "9e5a70ff5ca2c564b226b90ef30d3e9341edd478456fe347cc1a2b681d2da8a0";

export type GraphEditingWorkspace = {
  nodes: GraphNode[];
  edges: SemanticGraphEdge[];
  layout: Record<string, GraphPosition>;
  assignments: Record<string, string>;
  knowledgeBindings: Record<string, KnowledgeContextBindingDraft>;
};

export function createGraphEditingWorkspace(
  nodes: GraphNode[],
  edges: SemanticGraphEdge[],
  layout: Record<string, unknown> | undefined,
  assignments: Record<string, string> | undefined,
  knowledgeBindings: Record<string, KnowledgeContextBindingDraft> | undefined = undefined,
): GraphEditingWorkspace {
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
    knowledgeBindings: knowledgeBindings ?? {},
  };
}

export function createKnowledgeContextBinding(
  stagePath: string,
  retrievalPolicyRevisionId = "",
): KnowledgeContextBindingDraft {
  return {
    stage_path: stagePath,
    acwm_artifact_slot: "knowledge-context-v1",
    acwm_artifact_contract_version: KNOWLEDGE_CONTEXT_ARTIFACT_CONTRACT.version,
    acwm_artifact_contract_sha256: KNOWLEDGE_CONTEXT_ARTIFACT_CONTRACT_SHA256,
    retrieval_policy_revision_id: retrievalPolicyRevisionId,
    required: true,
    max_context_bytes: 65_536,
  };
}

export function configureWorkspaceKnowledgeContext(
  workspace: GraphEditingWorkspace,
  stagePath: string,
  binding: KnowledgeContextBindingDraft | null,
): GraphEditingWorkspace {
  const [outerId, innerId] = stagePath.split("/");
  let found = false;
  const updateStage = (stage: StageNode): StageNode => {
    found = true;
    const remaining = (stage.input_artifact_contracts ?? []).filter(
      (contract) => contract.id !== "knowledge-context-v1",
    );
    if (binding)
      return {
        ...stage,
        input_artifact_contracts: [
          ...remaining,
          KNOWLEDGE_CONTEXT_ARTIFACT_CONTRACT,
        ],
      };
    if (remaining.length > 0)
      return { ...stage, input_artifact_contracts: remaining };
    const { input_artifact_contracts: _removed, ...withoutContracts } = stage;
    return withoutContracts;
  };
  const nodes = workspace.nodes.map((node) => {
    if (!innerId && node.kind === "stage" && node.id === outerId)
      return updateStage(node);
    if (innerId && node.kind === "loop" && node.id === outerId)
      return {
        ...node,
        nodes: node.nodes.map((child) =>
          child.kind === "stage" && child.id === innerId
            ? updateStage(child)
            : child,
        ),
      };
    return node;
  });
  if (!found) return workspace;
  const knowledgeBindings = { ...workspace.knowledgeBindings };
  if (binding) knowledgeBindings[stagePath] = { ...binding, stage_path: stagePath };
  else delete knowledgeBindings[stagePath];
  return { ...workspace, nodes, knowledgeBindings };
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
    knowledgeBindings: Object.fromEntries(
      Object.entries(workspace.knowledgeBindings).filter(
        ([stagePath]) => stagePath !== nodeId && !stagePath.startsWith(`${nodeId}/`),
      ),
    ),
  };
}

function isPosition(value: unknown): value is GraphPosition {
  return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number";
}
