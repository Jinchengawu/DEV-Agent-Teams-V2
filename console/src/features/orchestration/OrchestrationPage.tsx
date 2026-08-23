import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type ReactFlowInstance,
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
type Deployment = components["schemas"]["AgentDeployment"];
type Provider = components["schemas"]["ProviderManifestView"];
type Position = { x: number; y: number };
type SemanticEdge = { source: string; target: string; condition?: string | null };
type StageNode = { id: string; kind: "stage"; workflow_mode: "agentscope.role-turn" | "code-delivery"; bindings: Record<string, string>; output_validator?: string | null };
type GateNode = { id: string; kind: "approval_gate"; subject_kind: string };
export type LoopNode = { id: string; kind: "loop"; policy: { exit_condition: string; max_iterations: number; timeout_seconds: number; on_exhausted: "fail" | "continue" | "needs_attention" }; nodes: Array<StageNode | GateNode>; edges: SemanticEdge[] };
export type GraphNode = StageNode | GateNode | LoopNode;

const stageSchema = z.object({ id: z.string(), kind: z.literal("stage"), workflow_mode: z.enum(["agentscope.role-turn", "code-delivery"]), bindings: z.record(z.string(), z.string()), output_validator: z.string().nullable().optional() });
const gateSchema = z.object({ id: z.string(), kind: z.literal("approval_gate"), subject_kind: z.string() });
const edgeSchema = z.object({ source: z.string(), target: z.string(), condition: z.string().nullable().optional() });
const loopSchema = z.object({ id: z.string(), kind: z.literal("loop"), policy: z.object({ exit_condition: z.string(), max_iterations: z.number(), timeout_seconds: z.number(), on_exhausted: z.enum(["fail", "continue", "needs_attention"]) }), nodes: z.array(z.union([stageSchema, gateSchema])), edges: z.array(edgeSchema) });
const definitionSchema = z.object({ id: z.string(), version: z.string(), nodes: z.array(z.union([stageSchema, gateSchema, loopSchema])), edges: z.array(edgeSchema) });

export function OrchestrationPage() {
  const client = useQueryClient();
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => request<Pipeline[]>("/v1/pipelines") });
  const deployments = useQuery({ queryKey: ["agent-deployments"], queryFn: () => request<Deployment[]>("/v1/agent-deployments") });
  const providers = useQuery({ queryKey: ["provider-manifests"], queryFn: () => request<Provider[]>("/v1/provider-manifests") });
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
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [connectionError, setConnectionError] = useState<string>();
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node, Edge>>();
  const [agentAssignments, setAgentAssignments] = useState<Record<string, string>>({});

  useEffect(() => {
    const next = parseDefinition(draft?.definition);
    setSemanticNodes(next.nodes);
    setNodes(createFlowNodes(next.nodes, draft?.layout));
    setEdges(createFlowEdges(next.edges));
    setSelectedNodeId(undefined);
    setSelectedEdgeId(undefined);
    setAgentAssignments(draft?.agent_assignments ?? {});
  }, [draft?.id, draft?.version, setEdges, setNodes]);
  useEffect(() => { if (flowInstance && nodes.length > 0) void flowInstance.fitView({ padding: 0.15, duration: 180 }); }, [flowInstance, nodes.length]);

  const createPipeline = useMutation({ mutationFn: (value: { id: string; name: string }) => request<{ pipeline: Pipeline; draft: Draft }>("/v1/pipelines", { method: "POST", body: JSON.stringify({ id: value.id, name: value.name, description: "", definition: { id: value.id, version: "4.0.0", nodes: [], edges: [] }, layout: {}, input_schema: {}, agent_assignments: {} }) }), onSuccess: async (created) => { await client.invalidateQueries({ queryKey: ["pipelines"] }); setSelectedPipelineId(created.pipeline.id); } });
  const save = useMutation({ mutationFn: () => request<Draft>(`/v1/pipeline-drafts/${draft?.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: draft?.version, definition: { id: selectedPipeline?.id, version: "4.0.0", nodes: semanticNodes, edges: semanticEdges(edges) }, layout: Object.fromEntries(nodes.map((node) => [node.id, node.position])), agent_assignments: agentAssignments }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipeline-drafts", selectedPipeline?.id] }) });
  const validate = useMutation({ mutationFn: () => request<Draft>(`/v1/pipeline-drafts/${draft?.id}/validate`, { method: "POST", body: JSON.stringify({ expected_version: draft?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipeline-drafts", selectedPipeline?.id] }) });
  const publish = useMutation({ mutationFn: () => request<Revision>(`/v1/pipeline-drafts/${draft?.id}/publish`, { method: "POST", body: JSON.stringify({ expected_version: draft?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }) });
  const activate = useMutation({ mutationFn: (target: Revision) => request<Pipeline>(`/v1/pipelines/${target.pipeline_id}/activate`, { method: "POST", body: JSON.stringify({ revision: target.revision, expected_version: selectedPipeline?.version }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }) });

  if (pipelines.isLoading || deployments.isLoading || providers.isLoading) return <LoadingState label="正在读取流水线、Agent 部署与 Provider…"/>;
  if (pipelines.error || deployments.error || providers.error) return <ErrorState error={(pipelines.error || deployments.error || providers.error)!}/>;

  const addNode = (kind: "role" | "code" | "gate" | "loop") => {
    if (!draft) return;
    const semantic = createGraphNode(kind, semanticNodes);
    setSemanticNodes((current) => [...current, semantic]);
    setNodes((current) => [...current, createFlowNode(semantic, nextPosition(current))]);
    setSelectedNodeId(semantic.id);
    setSelectedEdgeId(undefined);
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
  const selectedEdge = edges.find((item) => item.id === selectedEdgeId);
  const operationError = createPipeline.error || save.error || validate.error || publish.error || activate.error;

  return <div className="pipeline-orchestration">
    <aside className="pipeline-catalog panel"><div className="panel-head"><span>流水线目录</span><small>{pipelines.data?.length ?? 0} 条</small></div><PipelineCreator pending={createPipeline.isPending} onCreate={(id, name) => createPipeline.mutate({ id, name })}/><div className="pipeline-list">{pipelines.data?.map((pipeline) => <button className={pipeline.id === selectedPipeline?.id ? "selected" : ""} key={pipeline.id} onClick={() => setSelectedPipelineId(pipeline.id)}><b>{pipeline.name}</b><code>{pipeline.id}</code><small>{pipeline.active_revision ? `活动版本 R${pipeline.active_revision}` : "尚未激活版本"}</small></button>)}</div></aside>
    <section className="orchestration-main panel"><div className="panel-head"><span>{selectedPipeline?.name ?? "创建第一条流水线"}</span><small>{draft ? `草稿 V${draft.version} · ${draft.validation_status}` : "无草稿"}</small></div>
      {!selectedPipeline ? <div className="state-box"><div><Workflow size={24}/><b>还没有流水线</b><span>在左侧创建一条流水线后开始 DAG 编排。</span></div></div> : drafts.isLoading ? <LoadingState label="正在加载流水线草稿…"/> : !draft ? <div className="state-box"><b>该流水线没有可编辑草稿</b></div> : <><div className="orchestration-toolbar"><div className="node-forge" aria-label="新增图节点"><button onClick={() => addNode("role")}><Bot size={15}/>角色 Stage</button><button onClick={() => addNode("code")}><Code2 size={15}/>交付 Stage</button><button onClick={() => addNode("gate")}><ShieldQuestion size={15}/>审批 Gate</button><button onClick={() => addNode("loop")}><GitBranch size={15}/>有限 LOOP</button></div><span className="toolbar-separator"/><button onClick={() => save.mutate()}><Save size={15}/>保存图与布局</button><button onClick={() => validate.mutate()}><CheckCircle2 size={15}/>ACWM 图校验</button><button className="primary" disabled={draft.validation_status !== "valid"} onClick={() => publish.mutate()}>发布不可变版本</button>{publish.data && <button onClick={() => activate.mutate(publish.data)}>激活 R{publish.data.revision}</button>}</div>
        <DependencyCreator label="主图" nodes={semanticNodes} edges={semanticEdges(edges)} onAdd={(edge) => { setEdges((current) => [...current, edge]); setSelectedEdgeId(edge.id); setSelectedNodeId(undefined); }}/>
        <div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView onInit={setFlowInstance} nodesDraggable nodesConnectable onNodeClick={(_, node) => { setSelectedNodeId(node.id); setSelectedEdgeId(undefined); }} onEdgeClick={(_, edge) => { setSelectedEdgeId(edge.id); setSelectedNodeId(undefined); }} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} deleteKeyCode={["Backspace", "Delete"]}><Background gap={24}/><Controls/><MiniMap nodeColor={(node) => node.className?.toString().includes("loop") ? "#b875ff" : node.className?.toString().includes("gate") ? "#ff9b61" : "#38a3ff"}/></ReactFlow></div>{connectionError && <div className="validation-errors"><b>连线被拒绝</b><span>{connectionError}</span></div>}{draft.validation_errors.length > 0 && <div className="validation-errors"><b>ACWM 校验失败</b>{draft.validation_errors.map((error) => <span key={error}>{error}</span>)}</div>}<div className="revision-strip"><span>节点</span><code>{semanticNodes.length}</code><span>语义边</span><code>{edges.length}</code><span>活动图指纹</span><code>{revision.data?.fingerprint ?? "尚无活动版本"}</code></div>{revision.data && <PublishedBindingSnapshot revision={revision.data}/>}</>}{operationError && <ErrorState error={operationError}/>} </section>
    <aside className="orchestration-inspector panel"><div className="panel-head"><span>图语义检查器</span><small>{selectedNode?.id ?? selectedEdge?.id ?? "请选择节点或边"}</small></div>{selectedNode ? <GraphNodeInspector node={selectedNode} onChange={updateSelected} onDelete={removeSelected} assignments={agentAssignments} deployments={deployments.data ?? []} providers={providers.data ?? []} onAssignment={(site, deploymentId) => setAgentAssignments((current) => { const next = { ...current }; if (deploymentId) next[site] = deploymentId; else delete next[site]; return next; })}/> : selectedEdge ? <GraphEdgeInspector edge={selectedEdge} onConditionChange={(condition) => setEdges((current) => setEdgeCondition(current, selectedEdge.id, condition))} onDelete={() => { setEdges((current) => current.filter((item) => item.id !== selectedEdge.id)); setSelectedEdgeId(undefined); }}/> : <div className="inspector-placeholder"><GitBranch size={24}/><b>选择节点或连线</b><span>节点坐标只控制布局；连线与条件共同定义执行依赖。</span></div>}</aside>
  </div>;
}

function PipelineCreator({ pending, onCreate }: { pending: boolean; onCreate: (id: string, name: string) => void }) { const [id, setId] = useState(""); const [name, setName] = useState(""); return <div className="pipeline-create"><input aria-label="流水线 ID" placeholder="例如 release-review" value={id} onChange={(event) => setId(event.target.value)}/><input aria-label="流水线名称" placeholder="中文名称" value={name} onChange={(event) => setName(event.target.value)}/><button disabled={pending || !id || !name} onClick={() => { onCreate(id, name); setId(""); setName(""); }}><Plus size={14}/>创建流水线</button></div>; }

function DependencyCreator({ label, nodes, edges, onAdd }: { label: string; nodes: GraphNode[]; edges: SemanticEdge[]; onAdd: (edge: Edge) => void }) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [condition, setCondition] = useState("");
  const valid = Boolean(source && target && canConnectGraph(nodes, edges, source, target));
  return <div className="dependency-creator"><b>{label}依赖编辑器</b><label>{label}上游节点<select aria-label={`${label}上游节点`} value={source} onChange={(event) => setSource(event.target.value)}><option value="">请选择</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label><label>{label}下游节点<select aria-label={`${label}下游节点`} value={target} onChange={(event) => setTarget(event.target.value)}><option value="">请选择</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select></label><label>{label}分支条件<input aria-label={`${label}分支条件`} placeholder="可选" value={condition} onChange={(event) => setCondition(event.target.value)}/></label><button disabled={!valid} onClick={() => { onAdd(createDependencyEdge(source, target, condition)); setSource(""); setTarget(""); setCondition(""); }}>添加依赖边</button></div>;
}

function GraphNodeInspector({ node, onChange, onDelete, assignments, deployments, providers, onAssignment }: { node: GraphNode; onChange: (node: GraphNode) => void; onDelete: () => void; assignments: Record<string, string>; deployments: Deployment[]; providers: Provider[]; onAssignment: (site: string, deploymentId: string) => void }) {
  if (node.kind === "loop") return <div className="step-editor"><StatusBadge value="executing"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>有限 LOOP 子图</dd><dt>内部节点</dt><dd>{node.nodes.length}</dd></dl><label>退出条件策略<input value={node.policy.exit_condition} onChange={(event) => onChange({ ...node, policy: { ...node.policy, exit_condition: event.target.value } })}/></label><label>最大轮次<input type="number" min={1} max={100} value={node.policy.max_iterations} onChange={(event) => onChange({ ...node, policy: { ...node.policy, max_iterations: Number(event.target.value) } })}/></label><label>总超时（秒）<input type="number" min={1} value={node.policy.timeout_seconds} onChange={(event) => onChange({ ...node, policy: { ...node.policy, timeout_seconds: Number(event.target.value) } })}/></label><label>耗尽动作<select value={node.policy.on_exhausted} onChange={(event) => onChange({ ...node, policy: { ...node.policy, on_exhausted: event.target.value as LoopNode["policy"]["on_exhausted"] } })}><option value="fail">失败</option><option value="needs_attention">转人工处理</option><option value="continue">继续下游</option></select></label><LoopBodyEditor loop={node} onChange={onChange} assignments={assignments} deployments={deployments} providers={providers} onAssignment={onAssignment}/><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 LOOP</button></div>;
  if (node.kind === "approval_gate") return <div className="step-editor"><StatusBadge value="awaiting_plan_decision"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>全局审批 Gate</dd></dl><label>审批主题<select value={node.subject_kind} onChange={(event) => onChange({ ...node, subject_kind: event.target.value })}><option value="delivery-plan">交付计划审批</option><option value="candidate-change">候选变更审批</option></select></label><p className="field-hint">审批主题决定产品生成的不可变 Gate Subject 与允许执行的命令。</p><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 Gate</button></div>;
  const capability = Object.values(node.bindings)[0] ?? "";
  const site = `${node.id}.${stageSlot(node)}`;
  return <div className="step-editor"><StatusBadge value="executing"/><h2>{node.id}</h2><dl><dt>节点类型</dt><dd>Workflow Stage</dd><dt>执行模式</dt><dd>{node.workflow_mode}</dd></dl><label>Capability<input value={capability} onChange={(event) => { onChange(changeCapability(node, event.target.value)); onAssignment(site, ""); }}/></label><label>阶段模式<select value={node.workflow_mode} onChange={(event) => { onChange(changeStageMode(node, event.target.value as StageNode["workflow_mode"])); onAssignment(site, ""); }}><option value="agentscope.role-turn">AgentScope 角色执行</option><option value="code-delivery">受控代码交付</option></select></label><DeploymentAssignmentEditor site={site} capability={capability} value={assignments[site]} deployments={deployments} providers={providers} onChange={(deploymentId) => onAssignment(site, deploymentId)}/><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除 Stage</button></div>;
}

function GraphEdgeInspector({ edge, onConditionChange, onDelete }: { edge: Edge; onConditionChange: (condition: string) => void; onDelete: () => void }) {
  return <div className="step-editor"><StatusBadge value="queued"/><h2>{edge.source} → {edge.target}</h2><dl><dt>语义类型</dt><dd>有向依赖边</dd></dl><label>分支条件<input aria-label="分支条件" placeholder="留空表示无条件依赖" value={typeof edge.data?.condition === "string" ? edge.data.condition : ""} onChange={(event) => onConditionChange(event.target.value)}/></label><p className="field-hint">条件值必须对应上游节点声明的命名结果；ACWM 发布时会验证。</p><button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除连线</button></div>;
}

function LoopBodyEditor({ loop, onChange, assignments, deployments, providers, onAssignment }: { loop: LoopNode; onChange: (node: LoopNode) => void; assignments: Record<string, string>; deployments: Deployment[]; providers: Provider[]; onAssignment: (site: string, deploymentId: string) => void }) {
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [bodyNodes, setBodyNodes, onBodyNodesChange] = useNodesState<Node>(createLoopFlowNodes(loop.nodes));
  const [bodyEdges, setBodyEdges] = useEdgesState<Edge>(createFlowEdges(loop.edges));
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node, Edge>>();
  useEffect(() => { setBodyNodes(createLoopFlowNodes(loop.nodes)); setBodyEdges(createFlowEdges(loop.edges)); }, [loop.nodes, loop.edges, setBodyEdges, setBodyNodes]);
  useEffect(() => { if (flowInstance && bodyNodes.length > 0) void flowInstance.fitView({ padding: 0.2, duration: 150 }); }, [bodyNodes.length, flowInstance]);
  const add = (kind: "role" | "code" | "gate") => {
    const updated = addLoopBodyNode(loop, kind);
    onChange(updated);
    setSelectedNodeId(updated.nodes.at(-1)?.id);
    setSelectedEdgeId(undefined);
  };
  const connect = (connection: Connection) => {
    if (!connection.source || !connection.target || !canConnectGraph(loop.nodes, loop.edges, connection.source, connection.target)) return;
    onChange({ ...loop, edges: [...loop.edges, { source: connection.source, target: connection.target }] });
  };
  const changeEdges = (changes: EdgeChange[]) => {
    setBodyEdges((current) => {
      const updated = applyEdgeChanges(changes, current);
      onChange({ ...loop, edges: semanticEdges(updated) });
      return updated;
    });
  };
  const selectedEdge = bodyEdges.find((edge) => edge.id === selectedEdgeId);
  const selectedBodyNode = loop.nodes.find((node) => node.id === selectedNodeId);
  const selectedSite = selectedBodyNode?.kind === "stage" ? `${loop.id}/${selectedBodyNode.id}.${stageSlot(selectedBodyNode)}` : undefined;
  return <section className="loop-body-editor"><div className="loop-body-head"><b>LOOP 内部 DAG</b><small>{loop.nodes.length} 节点 · {loop.edges.length} 边</small></div><div className="node-forge"><button onClick={() => add("role")}>角色 Stage</button><button onClick={() => add("code")}>交付 Stage</button></div><p className="field-hint">当前版本只允许循环体执行机器节点；人工审批 Gate 必须放在 LOOP 外部。</p><DependencyCreator label="循环体" nodes={loop.nodes} edges={loop.edges} onAdd={(edge) => onChange({ ...loop, edges: [...loop.edges, ...semanticEdges([edge])] })}/><div className="loop-flow"><ReactFlow nodes={bodyNodes} edges={bodyEdges} fitView onInit={setFlowInstance} onNodesChange={onBodyNodesChange} onEdgesChange={changeEdges} onConnect={connect} onNodeClick={(_, node) => { setSelectedNodeId(node.id); setSelectedEdgeId(undefined); }} onEdgeClick={(_, edge) => { setSelectedEdgeId(edge.id); setSelectedNodeId(undefined); }} deleteKeyCode={null}><Background gap={16}/><Controls showInteractive={false}/></ReactFlow></div>{selectedBodyNode?.kind === "stage" && selectedSite && <DeploymentAssignmentEditor site={selectedSite} capability={Object.values(selectedBodyNode.bindings)[0] ?? ""} value={assignments[selectedSite]} deployments={deployments} providers={providers} onChange={(deploymentId) => onAssignment(selectedSite, deploymentId)}/>} {selectedNodeId && <button className="danger" onClick={() => { if (selectedSite) onAssignment(selectedSite, ""); onChange(removeLoopBodyNode(loop, selectedNodeId)); setSelectedNodeId(undefined); }}>删除内部节点 {selectedNodeId}</button>}{selectedEdge && <label>内部边条件<input aria-label="内部边条件" value={typeof selectedEdge.data?.condition === "string" ? selectedEdge.data.condition : ""} onChange={(event) => { const updated = setEdgeCondition(bodyEdges, selectedEdge.id, event.target.value); setBodyEdges(updated); onChange({ ...loop, edges: semanticEdges(updated) }); }}/></label>}</section>;
}

function DeploymentAssignmentEditor({ site, capability, value, deployments, providers, onChange }: { site: string; capability: string; value?: string; deployments: Deployment[]; providers: Provider[]; onChange: (deploymentId: string) => void }) {
  const compatibleProviderIds = new Set(providers.filter((provider) => provider.capabilities.some((item) => item.id === capability)).map((provider) => provider.id));
  const selectable = deployments.filter((item) => isDeploymentSelectable(item, compatibleProviderIds, capability));
  const unavailable = deployments.filter((item) => !selectable.some((candidate) => candidate.id === item.id));
  const selected = deployments.find((item) => item.id === value);
  return <div className="deployment-assignment"><label>Agent Deployment<select aria-label={`Agent Deployment ${site}`} value={value ?? ""} onChange={(event) => onChange(event.target.value)}><option value="">请选择已通过资格检查的部署</option>{selectable.length > 0 && <optgroup label="可选择">{selectable.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.profile_id} → {item.instance_id}</option>)}</optgroup>}{unavailable.length > 0 && <optgroup label="不可选择">{unavailable.map((item) => <option key={item.id} value={item.id} disabled>{item.name} · {deploymentUnavailableReason(item, compatibleProviderIds, capability)}</option>)}</optgroup>}</select></label><dl><dt>Binding Site</dt><dd><code>{site}</code></dd><dt>Capability</dt><dd><code>{capability || "未配置"}</code></dd>{selected && <><dt>冻结来源</dt><dd>{selected.profile_id} R{selected.profile_revision} · {selected.provider_id}</dd></>}</dl>{selectable.length === 0 && <p className="field-hint">没有兼容且已启用的 Deployment。请先到“智能体实例 → Agent 部署”完成资格检查。</p>}</div>;
}

function deploymentUnavailableReason(item: Deployment, compatibleProviderIds: Set<string>, capability: string): string { if (!item.capability_requirements.some((requirement) => requirement.id === capability)) return "Agent Profile 未声明当前 Capability"; if (!compatibleProviderIds.has(item.provider_id)) return "Provider 不支持当前 Capability"; if (item.qualification_status !== "qualified") return "资格检查未通过"; if (!item.enabled) return "部署未启用"; return "当前不可用"; }
export function isDeploymentSelectable(item: Deployment, compatibleProviderIds: Set<string>, capability: string): boolean { return item.enabled && item.qualification_status === "qualified" && compatibleProviderIds.has(item.provider_id) && item.capability_requirements.some((requirement) => requirement.id === capability); }
function stageSlot(node: StageNode): string { return Object.keys(node.bindings)[0] ?? (node.workflow_mode === "code-delivery" ? "developer" : "actor"); }

function PublishedBindingSnapshot({ revision }: { revision: Revision }) {
  const bindings = Object.entries(revision.resolved_provider_bindings ?? {});
  return <section className="binding-snapshot"><b>已发布执行快照 · {revision.binding_model}</b>{bindings.length === 0 ? <p className="field-hint">该活动版本使用旧版全局绑定；仅用于历史兼容。</p> : bindings.map(([site, raw]) => { const deployment = isRecord(raw.deployment) ? raw.deployment : {}; const binding = isRecord(raw.binding) ? raw.binding : {}; return <span key={site}><code>{site}</code><small>{stringValue(deployment.profile_id)} → {stringValue(deployment.instance_id)} · {stringValue(binding.binding_fingerprint).slice(0, 12)}</small></span>; })}</section>;
}

function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function stringValue(value: unknown): string { return typeof value === "string" ? value : "未知"; }

export function createGraphNode(kind: "role" | "code" | "gate" | "loop", existing: GraphNode[]): GraphNode { const prefix = kind === "role" ? "stage" : kind; const id = nextId(prefix, existing.map((item) => item.id)); if (kind === "gate") { const gateCount = existing.filter((item) => item.kind === "approval_gate").length; return { id, kind: "approval_gate", subject_kind: gateCount === 0 ? "delivery-plan" : "candidate-change" }; } if (kind === "loop") return { id, kind: "loop", policy: { exit_condition: "machine-tests-passed", max_iterations: 3, timeout_seconds: 300, on_exhausted: "fail" }, nodes: [{ id: `${id}-work`, kind: "stage", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" }], edges: [] }; if (kind === "code") return { id, kind: "stage", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" }; return { id, kind: "stage", workflow_mode: "agentscope.role-turn", bindings: { actor: "hermes-pm" } }; }
export function canConnectGraph(nodes: GraphNode[], edges: SemanticEdge[], source: string, target: string): boolean { if (source === target || !nodes.some((node) => node.id === source) || !nodes.some((node) => node.id === target)) return false; if (edges.some((edge) => edge.source === source && edge.target === target)) return false; const adjacency = new Map<string, string[]>(); for (const edge of [...edges, { source, target }]) adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]); const visit = (current: string, seen: Set<string>): boolean => { if (current === source) return true; if (seen.has(current)) return false; seen.add(current); return (adjacency.get(current) ?? []).some((next) => visit(next, seen)); }; return !visit(target, new Set()); }
export function changeStageMode(node: StageNode, mode: StageNode["workflow_mode"]): StageNode { return mode === "code-delivery" ? { ...node, workflow_mode: mode, bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" } : { ...node, workflow_mode: mode, bindings: { actor: "hermes-pm" }, output_validator: undefined }; }
export function changeCapability(node: StageNode, capability: string): StageNode { return { ...node, bindings: { [node.workflow_mode === "code-delivery" ? "developer" : "actor"]: capability } }; }
export function setEdgeCondition(edges: Edge[], edgeId: string, condition: string): Edge[] { const normalized = condition.trim() || undefined; return edges.map((edge) => edge.id === edgeId ? { ...edge, data: { ...edge.data, condition: normalized }, label: normalized } : edge); }
export function createDependencyEdge(source: string, target: string, condition: string): Edge { const normalized = condition.trim() || undefined; return { id: `edge-${source}-${target}`, source, target, type: "smoothstep", data: { condition: normalized }, label: normalized }; }
export function addLoopBodyNode(loop: LoopNode, kind: "role" | "code" | "gate"): LoopNode { const created = createGraphNode(kind, loop.nodes); if (created.kind === "loop") return loop; return { ...loop, nodes: [...loop.nodes, created] }; }
export function removeLoopBodyNode(loop: LoopNode, nodeId: string): LoopNode { return { ...loop, nodes: loop.nodes.filter((node) => node.id !== nodeId), edges: loop.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId) }; }
export function parseDefinition(value: unknown): { id: string; version: string; nodes: GraphNode[]; edges: SemanticEdge[] } { const parsed = definitionSchema.safeParse(value); return parsed.success ? parsed.data : { id: "", version: "4.0.0", nodes: [], edges: [] }; }
function semanticEdges(edges: Edge[]): SemanticEdge[] { return edges.map((edge) => ({ source: edge.source, target: edge.target, condition: typeof edge.data?.condition === "string" ? edge.data.condition : undefined })); }
function createFlowEdges(edges: SemanticEdge[]): Edge[] { return edges.map((edge) => ({ id: `edge-${edge.source}-${edge.target}`, source: edge.source, target: edge.target, type: "smoothstep", data: { condition: edge.condition }, label: edge.condition || undefined })); }
function createFlowNodes(nodes: GraphNode[], layout?: Record<string, unknown>): Node[] { return nodes.map((node, index) => createFlowNode(node, isPosition(layout?.[node.id]) ? layout[node.id] as Position : { x: index * 230, y: index % 2 ? 170 : 40 })); }
function createLoopFlowNodes(nodes: Array<StageNode | GateNode>): Node[] { return nodes.map((node, index) => createFlowNode(node, { x: index * 145, y: 55 })); }
function createFlowNode(node: GraphNode, position: Position): Node { const type = node.kind === "loop" ? "有限 LOOP" : node.kind === "approval_gate" ? "审批 Gate" : node.workflow_mode === "code-delivery" ? "代码交付 Stage" : "角色 Stage"; return { id: node.id, position, data: { label: <><small>{type}</small><b>{node.id}</b></> }, className: `flow-node ${node.kind === "loop" ? "loop" : node.kind === "approval_gate" ? "gate" : "stage"}` }; }
function nextPosition(nodes: Node[]): Position { const last = nodes.at(-1); return { x: last ? last.position.x + 220 : 40, y: nodes.length % 2 ? 170 : 40 }; }
function isPosition(value: unknown): value is Position { return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number"; }
function nextId(prefix: string, ids: string[]): string { let index = 1; while (ids.includes(`${prefix}-${index}`)) index += 1; return `${prefix}-${index}`; }
