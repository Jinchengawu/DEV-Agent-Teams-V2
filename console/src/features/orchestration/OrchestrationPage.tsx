import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import { Bot, CheckCircle2, Code2, GitBranch, Plus, Save, ShieldQuestion, Trash2, Workflow } from "lucide-react";
import { z } from "zod";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";

type Pipeline = components["schemas"]["Pipeline"];
type Draft = components["schemas"]["PipelineDraft"];
type Revision = components["schemas"]["PipelineRevision"];
type Position = { x: number; y: number };
type SemanticEdge = { source: string; target: string; condition?: string | null };
type StageNode = { id: string; kind: "stage"; workflow_mode: "agentscope.role-turn" | "code-delivery"; bindings: Record<string, string>; output_validator?: string | null };
type GateNode = { id: string; kind: "approval_gate"; subject_kind: string };
type LoopNode = { id: string; kind: "loop"; policy: { exit_condition: string; max_iterations: number; timeout_seconds: number; on_exhausted: "fail" | "continue" | "needs_attention" }; nodes: Array<StageNode | GateNode>; edges: SemanticEdge[] };
export type GraphNode = StageNode | GateNode | LoopNode;

const stageSchema = z.object({ id: z.string(), kind: z.literal("stage"), workflow_mode: z.enum(["agentscope.role-turn", "code-delivery"]), bindings: z.record(z.string(), z.string()), output_validator: z.string().nullable().optional() });
const gateSchema = z.object({ id: z.string(), kind: z.literal("approval_gate"), subject_kind: z.string() });
const edgeSchema = z.object({ source: z.string(), target: z.string(), condition: z.string().nullable().optional() });
const loopSchema = z.object({ id: z.string(), kind: z.literal("loop"), policy: z.object({ exit_condition: z.string(), max_iterations: z.number(), timeout_seconds: z.number(), on_exhausted: z.enum(["fail", "continue", "needs_attention"]) }), nodes: z.array(z.union([stageSchema, gateSchema])), edges: z.array(edgeSchema) });
const definitionSchema = z.object({ id: z.string(), version: z.string(), nodes: z.array(z.union([stageSchema, gateSchema, loopSchema])), edges: z.array(edgeSchema) });

export function OrchestrationPage() {
  const client = useQueryClient();
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => request<Pipeline[]>("/v1/pipelines") });
  const [selectedPipelineId, setSelectedPipelineId] = useState<string>();
  const selectedPipeline = pipelines.data?.find((item) => item.id === selectedPipelineId) ?? pipelines.data?.[0];
  useEffect(() => { if (!selectedPipelineId && pipelines.data?.[0]) setSelectedPipelineId(pipelines.data[0].id); }, [pipelines.data, selectedPipelineId]);
  const drafts = useQuery({ queryKey: ["pipeline-drafts", selectedPipeline?.id], enabled: Boolean(selectedPipeline), queryFn: () => request<Draft[]>(`/v1/pipelines/${selectedPipeline?.id}/drafts`) });
  const draft = drafts.data?.[0];
  const revision = useQuery({ queryKey: ["pipeline-revision", selectedPipeline?.id, selectedPipeline?.active_revision], enabled: Boolean(selectedPipeline?.active_revision), queryFn: () => request<Revision>(`/v1/pipelines/${selectedPipeline?.id}/revisions/${selectedPipeline?.active_revision}`) });
  const parsed = useMemo(() => parseDefinition(draft?.definition), [draft?.definition]);
  const [semanticNodes, setSemanticNodes] = useState<GraphNode[]>(parsed.nodes);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(createFlowNodes(parsed.nodes, draft?.layout));
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(createFlowEdges(parsed.edges));
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [connectionError, setConnectionError] = useState<string>();

  useEffect(() => {
    const next = parseDefinition(draft?.definition);
    setSemanticNodes(next.nodes);
    setNodes(createFlowNodes(next.nodes, draft?.layout));
    setEdges(createFlowEdges(next.edges));
    setSelectedNodeId(undefined);
  }, [draft?.id, draft?.version, setEdges, setNodes]);

  const createPipeline = useMutation({ mutationFn: (value: { id: string; name: string }) => request<{ pipeline: Pipeline; draft: Draft }>("/v1/pipelines", { method: "POST", body: JSON.stringify({ id: value.id, name: value.name, description: "", definition: { id: value.id, version: "4.0.0", nodes: [], edges: [] }, layout: {}, input_schema: {} }) }), onSuccess: async (created) => { await client.invalidateQueries({ queryKey: ["pipelines"] }); setSelectedPipelineId(created.pipeline.id); } });
  const save = useMutation({ mutationFn: () => request<Draft>(`/v1/pipeline-drafts/${draft?.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: draft?.version, definition: { id: selectedPipeline?.id, version: "4.0.0", nodes: semanticNodes, edges: semanticEdges(edges) }, layout: Object.fromEntries(nodes.map((node) => [node.id, node.position])) }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipeline-drafts", selectedPipeline?.id] }) });
  const validate = useMutation({ mutationFn: () => request<Draft>(`/v1/pipeline-drafts/${draft?.id}/validate`, { method: "POST", body: JSON.stringify({ expected_version: draft?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipeline-drafts", selectedPipeline?.id] }) });
  const publish = useMutation({ mutationFn: () => request<Revision>(`/v1/pipeline-drafts/${draft?.id}/publish`, { method: "POST", body: JSON.stringify({ expected_version: draft?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }) });
  const activate = useMutation({ mutationFn: (target: Revision) => request<Pipeline>(`/v1/pipelines/${target.pipeline_id}/activate`, { method: "POST", body: JSON.stringify({ revision: target.revision, expected_version: selectedPipeline?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }) });

  if (pipelines.isLoading) return <LoadingState label="正在读取流水线目录…"/>;
  if (pipelines.error) return <ErrorState error={pipelines.error}/>;

  const addNode = (kind: "role" | "code" | "gate" | "loop") => {
    if (!draft) return;
    const semantic = createGraphNode(kind, semanticNodes);
    setSemanticNodes((current) => [...current, semantic]);
    setNodes((current) => [...current, createFlowNode(semantic, nextPosition(current))]);
    setSelectedNodeId(semantic.id);
  };
  const connect = (connection: Connection) => {
    if (!connection.source || !connection.target) return;
    if (!canConnectGraph(semanticNodes, semanticEdges(edges), connection.source, connection.target)) {
      setConnectionError("该连线会形成自环、重复依赖或 DAG 环路，已拒绝保存。"); return;
    }
    setConnectionError(undefined);
    setEdges((current) => addEdge({ ...connection, id: `edge-${connection.source}-${connection.target}`, type: "smoothstep" }, current));
  };
  const updateSelected = (updated: GraphNode) => {
    setSemanticNodes((current) => current.map((item) => item.id === updated.id ? updated : item));
    setNodes((current) => current.map((item) => item.id === updated.id ? createFlowNode(updated, item.position) : item));
  };
  const removeSelected = () => {
    if (!selectedNodeId) return;
    setSemanticNodes((current) => current.filter((item) => item.id !== selectedNodeId));
    setNodes((current) => current.filter((item) => item.id !== selectedNodeId));
    setEdges((current) => current.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId(undefined);
  };
  const selectedNode = semanticNodes.find((item) => item.id === selectedNodeId);
  const operationError = createPipeline.error || save.error || validate.error || publish.error || activate.error;

  return <div className="pipeline-orchestration">
    <aside className="pipeline-catalog panel"><div className="panel-head"><span>流水线目录</span><small>{pipelines.data?.length ?? 0} 条</small></div><PipelineCreator pending={createPipeline.isPending} onCreate={(id, name) => createPipeline.mutate({ id, name })}/><div className="pipeline-list">{pipelines.data?.map((pipeline) => <button className={pipeline.id === selectedPipeline?.id ? "selected" : ""} key={pipeline.id} onClick={() => setSelectedPipelineId(pipeline.id)}><b>{pipeline.name}</b><code>{pipeline.id}</code><small>{pipeline.active_revision ? `活动版本 R${pipeline.active_revision}` : "尚未激活版本"}</small></button>)}</div></aside>
    <section className="orchestration-main panel"><div className="panel-head"><span>{selectedPipeline?.name ?? "创建第一条流水线"}</span><small>{draft ? `草稿 V${draft.version} · ${draft.validation_status}` : "无草稿"}</small></div>
      {!selectedPipeline ? <div className="state-box"><div><Workflow size={24}/><b>还没有流水线</b><span>在左侧创建一条流水线后开始 DAG 编排。</span></div></div> : drafts.isLoading ? <LoadingState label="正在加载流水线草稿…"/> : !draft ? <div className="state-box"><b>该流水线没有可编辑草稿</b></div> : <><div className="orchestration-toolbar"><div className="node-forge" aria-label="新增图节点"><button onClick={() => addNode("role")}><Bot size={15}/>角色 Stage</button><button onClick={() => addNode("code")}><Code2 size={15}/>交付 Stage</button><button onClick={() => addNode("gate")}><ShieldQuestion size={15}/>审批 Gate</button><button onClick={() => addNode("loop")}><GitBranch size={15}/>有限 LOOP</button></div><span className="toolbar-separator"/><button onClick={() => save.mutate()}><Save size={15}/>保存图与布局</button><button onClick={() => validate.mutate()}><CheckCircle2 size={15}/>ACWM 图校验</button><button className="primary" disabled={draft.validation_status !== "valid"} onClick={() => publish.mutate()}>发布不可变版本</button>{publish.data && <button onClick={() => activate.mutate(publish.data)}>激活 R{publish.data.revision}</button>}</div>
        <div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable nodesConnectable onNodeClick={(_, node) => setSelectedNodeId(node.id)} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} deleteKeyCode={["Backspace", "Delete"]}><Background gap={24}/><Controls/><MiniMap nodeColor={(node) => node.className?.toString().includes("loop") ? "#b875ff" : node.className?.toString().includes("gate") ? "#ff9b61" : "#38a3ff"}/></ReactFlow></div>{connectionError && <div className="validation-errors"><b>连线被拒绝</b><span>{connectionError}</span></div>}{draft.validation_errors.length > 0 && <div className="validation-errors"><b>ACWM 校验失败</b>{draft.validation_errors.map((error) => <span key={error}>{error}</span>)}</div>}<div className="revision-strip"><span>节点</span><code>{semanticNodes.length}</code><span>语义边</span><code>{edges.length}</code><span>活动图指纹</span><code>{revision.data?.fingerprint ?? "尚无活动版本"}</code></div></>}{operationError && <ErrorState error={operationError}/>} </section>
    <aside className="orchestration-inspector panel"><div className="panel-head"><span>图语义检查器</span><small>{selectedNode?.id ?? "请选择节点"}</small></div>{selectedNode ? <GraphNodeInspector node={selectedNode} onChange={updateSelected} onDelete={removeSelected}/> : <div className="inspector-placeholder"><GitBranch size={24}/><b>选择节点或创建连线</b><span>节点坐标只控制布局；连线才定义执行依赖。</span></div>}</aside>
  </div>;
}

function PipelineCreator({ pending, onCreate }: { pending: boolean; onCreate: (id: string, name: string) => void }) { const [id, setId] = useState(""); const [name, setName] = useState(""); return <div className="pipeline-create"><input aria-label="流水线 ID" placeholder="例如 release-review" value={id} onChange={(event) => setId(event.target.value)}/><input aria-label="流水线名称" placeholder="中文名称" value={name} onChange={(event) => setName(event.target.value)}/><button disabled={pending || !id || !name} onClick={() => { onCreate(id, name); setId(""); setName(""); }}><Plus size={14}/>创建流水线</button></div>; }

function GraphNodeInspector({ node, onChange, onDelete }: { node: GraphNode; onChange: (node: GraphNode) => void; onDelete: () => void }) {
  if (node.kind === "loop") return <div className="step-editor"><StatusBadge value="executing"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>有限 LOOP 子图</dd><dt>内部节点</dt><dd>{node.nodes.length}</dd></dl><label>退出条件策略<input value={node.policy.exit_condition} onChange={(event) => onChange({ ...node, policy: { ...node.policy, exit_condition: event.target.value } })}/></label><label>最大轮次<input type="number" min={1} max={100} value={node.policy.max_iterations} onChange={(event) => onChange({ ...node, policy: { ...node.policy, max_iterations: Number(event.target.value) } })}/></label><label>总超时（秒）<input type="number" min={1} value={node.policy.timeout_seconds} onChange={(event) => onChange({ ...node, policy: { ...node.policy, timeout_seconds: Number(event.target.value) } })}/></label><label>耗尽动作<select value={node.policy.on_exhausted} onChange={(event) => onChange({ ...node, policy: { ...node.policy, on_exhausted: event.target.value as LoopNode["policy"]["on_exhausted"] } })}><option value="fail">失败</option><option value="needs_attention">转人工处理</option><option value="continue">继续下游</option></select></label><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 LOOP</button></div>;
  if (node.kind === "approval_gate") return <div className="step-editor"><StatusBadge value="awaiting_plan_decision"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>全局审批 Gate</dd></dl><label>审批主题<input value={node.subject_kind} onChange={(event) => onChange({ ...node, subject_kind: event.target.value })}/></label><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 Gate</button></div>;
  const capability = Object.values(node.bindings)[0] ?? "";
  return <div className="step-editor"><StatusBadge value="executing"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>Workflow Stage</dd><dt>执行模式</dt><dd>{node.workflow_mode}</dd></dl><label>Capability<input value={capability} onChange={(event) => onChange(changeCapability(node, event.target.value))}/></label><label>阶段模式<select value={node.workflow_mode} onChange={(event) => onChange(changeStageMode(node, event.target.value as StageNode["workflow_mode"]))}><option value="agentscope.role-turn">AgentScope 角色执行</option><option value="code-delivery">受控代码交付</option></select></label><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 Stage</button></div>;
}

export function createGraphNode(kind: "role" | "code" | "gate" | "loop", existing: GraphNode[]): GraphNode { const prefix = kind === "role" ? "stage" : kind; const id = nextId(prefix, existing.map((item) => item.id)); if (kind === "gate") return { id, kind: "approval_gate", subject_kind: "artifact" }; if (kind === "loop") return { id, kind: "loop", policy: { exit_condition: "machine-tests-passed", max_iterations: 3, timeout_seconds: 300, on_exhausted: "fail" }, nodes: [{ id: `${id}-work`, kind: "stage", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" }], edges: [] }; if (kind === "code") return { id, kind: "stage", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" }; return { id, kind: "stage", workflow_mode: "agentscope.role-turn", bindings: { actor: "hermes-pm" } }; }
export function canConnectGraph(nodes: GraphNode[], edges: SemanticEdge[], source: string, target: string): boolean { if (source === target || !nodes.some((node) => node.id === source) || !nodes.some((node) => node.id === target)) return false; if (edges.some((edge) => edge.source === source && edge.target === target)) return false; const adjacency = new Map<string, string[]>(); for (const edge of [...edges, { source, target }]) adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]); const visit = (current: string, seen: Set<string>): boolean => { if (current === source) return true; if (seen.has(current)) return false; seen.add(current); return (adjacency.get(current) ?? []).some((next) => visit(next, seen)); }; return !visit(target, new Set()); }
export function changeStageMode(node: StageNode, mode: StageNode["workflow_mode"]): StageNode { return mode === "code-delivery" ? { ...node, workflow_mode: mode, bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" } : { ...node, workflow_mode: mode, bindings: { actor: "hermes-pm" }, output_validator: undefined }; }
export function changeCapability(node: StageNode, capability: string): StageNode { return { ...node, bindings: { [node.workflow_mode === "code-delivery" ? "developer" : "actor"]: capability } }; }
export function parseDefinition(value: unknown): { id: string; version: string; nodes: GraphNode[]; edges: SemanticEdge[] } { const parsed = definitionSchema.safeParse(value); return parsed.success ? parsed.data : { id: "", version: "4.0.0", nodes: [], edges: [] }; }
function semanticEdges(edges: Edge[]): SemanticEdge[] { return edges.map((edge) => ({ source: edge.source, target: edge.target, condition: typeof edge.data?.condition === "string" ? edge.data.condition : undefined })); }
function createFlowEdges(edges: SemanticEdge[]): Edge[] { return edges.map((edge) => ({ id: `edge-${edge.source}-${edge.target}`, source: edge.source, target: edge.target, type: "smoothstep", data: { condition: edge.condition } })); }
function createFlowNodes(nodes: GraphNode[], layout?: Record<string, unknown>): Node[] { return nodes.map((node, index) => createFlowNode(node, isPosition(layout?.[node.id]) ? layout[node.id] as Position : { x: index * 230, y: index % 2 ? 170 : 40 })); }
function createFlowNode(node: GraphNode, position: Position): Node { const type = node.kind === "loop" ? "有限 LOOP" : node.kind === "approval_gate" ? "审批 Gate" : node.workflow_mode === "code-delivery" ? "代码交付 Stage" : "角色 Stage"; return { id: node.id, position, data: { label: <><small>{type}</small><b>{node.id}</b></> }, className: `flow-node ${node.kind === "loop" ? "loop" : node.kind === "approval_gate" ? "gate" : "stage"}` }; }
function nextPosition(nodes: Node[]): Position { const last = nodes.at(-1); return { x: last ? last.position.x + 220 : 40, y: nodes.length % 2 ? 170 : 40 }; }
function isPosition(value: unknown): value is Position { return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number"; }
function nextId(prefix: string, ids: string[]): string { let index = 1; while (ids.includes(`${prefix}-${index}`)) index += 1; return `${prefix}-${index}`; }
