import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, MiniMap, ReactFlow, useNodesState, type Edge, type Node, type NodeChange } from "@xyflow/react";
import { Bot, CheckCircle2, Code2, GitCompareArrows, Redo2, Save, ShieldQuestion, Trash2, Undo2 } from "lucide-react";
import { z } from "zod";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { journeyStepLabel } from "../../i18n";

type Draft = components["schemas"]["JourneyDraft"];
type Revision = components["schemas"]["JourneyRevision"];
type Binding = components["schemas"]["CapabilityBinding"];
type Position = { x: number; y: number };
type StageStep = { id: string; kind: "stage"; workflow_mode: "agentscope.role-turn" | "code-delivery"; bindings: Record<string, string>; output_validator?: string; [key: string]: unknown };
type GateStep = { id: string; kind: "approval_gate"; subject_kind: "delivery-plan" | "candidate-change"; [key: string]: unknown };
type Step = StageStep | GateStep;

const stageSchema = z.object({ id: z.string(), kind: z.literal("stage"), workflow_mode: z.enum(["agentscope.role-turn", "code-delivery"]), bindings: z.record(z.string(), z.string()), output_validator: z.string().optional() }).passthrough();
const gateSchema = z.object({ id: z.string(), kind: z.literal("approval_gate"), subject_kind: z.enum(["delivery-plan", "candidate-change"]) }).passthrough();
const definitionSchema = z.object({ steps: z.array(z.discriminatedUnion("kind", [stageSchema, gateSchema])) }).passthrough();

export function OrchestrationPage() {
  const client = useQueryClient();
  const journeys = useQuery({ queryKey: ["journeys"], queryFn: () => request<Revision[]>("/v1/journeys") });
  const drafts = useQuery({ queryKey: ["journey-drafts"], queryFn: () => request<Draft[]>("/v1/journey-drafts") });
  const bindings = useQuery({ queryKey: ["capability-bindings"], queryFn: () => request<Binding[]>("/v1/capability-bindings") });
  const revision = journeys.data?.[0];
  const latestDraft = drafts.data?.at(-1);
  const [viewMode, setViewMode] = useState<"draft" | "published">("draft");
  const draft = viewMode === "draft" ? latestDraft : undefined;
  const source = draft?.definition ?? revision?.definition;
  const sourceSteps = useMemo<Step[]>(() => {
    const parsed = definitionSchema.safeParse(source);
    return parsed.success ? parsed.data.steps : [];
  }, [source]);
  const [steps, setSteps] = useState<Step[]>(sourceSteps);
  const [selectedId, setSelectedId] = useState<string>();
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<Node>(createNodes(sourceSteps, draft?.layout));
  const history = useRef<Node[][]>([]);
  const future = useRef<Node[][]>([]);

  useEffect(() => {
    setSteps(sourceSteps);
    setNodes(createNodes(sourceSteps, draft?.layout));
    setSelectedId(undefined);
    history.current = [];
    future.current = [];
  }, [draft?.id, draft?.version, revision?.journey_id, revision?.revision, setNodes, sourceSteps]);

  const edges = useMemo<Edge[]>(() => {
    const ordered = orderedNodes(nodes);
    return ordered.slice(1).map((node, index) => ({ id: `linear-${ordered[index].id}-${node.id}`, source: ordered[index].id, target: node.id, animated: Boolean(draft), type: "smoothstep" }));
  }, [draft, nodes]);

  const clone = useMutation({ mutationFn: () => request<Draft>("/v1/journey-drafts", { method: "POST", body: JSON.stringify({ name: "后端交付旅程草稿", definition: revision?.definition, layout: {} }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }) });
  const save = useMutation({
    mutationFn: () => request<Draft>(`/v1/journey-drafts/${draft?.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: draft?.version, definition: { ...(source ?? {}), steps: orderedNodes(nodes).map((node) => steps.find((step) => step.id === node.id)).filter((step): step is Step => Boolean(step)) }, layout: Object.fromEntries(nodes.map((node) => [node.id, node.position])) }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }),
  });
  const validate = useMutation({ mutationFn: () => request<Draft>(`/v1/journey-drafts/${draft?.id}/validate`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }) });
  const publish = useMutation({ mutationFn: () => request<Revision>(`/v1/journey-drafts/${draft?.id}/publish`, { method: "POST" }), onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ["journeys"] }), client.invalidateQueries({ queryKey: ["journey-drafts"] })]); setViewMode("published"); } });

  if (journeys.isLoading || drafts.isLoading || bindings.isLoading) return <LoadingState label="正在解析 ACWM 旅程、草稿与能力绑定…"/>;
  if (journeys.error || drafts.error || bindings.error) return <ErrorState error={(journeys.error || drafts.error || bindings.error)!}/>;

  const onNodesChange = (changes: NodeChange<Node>[]) => {
    if (changes.some((change) => change.type === "position" && change.dragging)) {
      if (history.current.at(-1) !== nodes) history.current.push(nodes.map(copyNode));
      future.current = [];
    }
    onNodesChangeBase(changes);
  };
  const undo = () => { const previous = history.current.pop(); if (!previous) return; future.current.push(nodes.map(copyNode)); setNodes(previous); };
  const redo = () => { const next = future.current.pop(); if (!next) return; history.current.push(nodes.map(copyNode)); setNodes(next); };
  const selectedStep = steps.find((step) => step.id === selectedId);
  const operationError = save.error || validate.error || publish.error || clone.error;
  const addStep = (kind: "role" | "code" | "gate") => {
    if (!draft) return;
    const step = createStep(kind, steps);
    const position = nextNodePosition(nodes);
    setSteps((current) => [...current, step]);
    setNodes((current) => [...current, createNode(step, position)]);
    setSelectedId(step.id);
  };
  const updateSelected = (updated: Step) => {
    setSteps((current) => current.map((step) => step.id === updated.id ? updated : step));
    setNodes((current) => current.map((node) => node.id === updated.id ? createNode(updated, node.position) : node));
  };
  const deleteSelected = () => {
    if (!selectedId || !draft) return;
    setSteps((current) => current.filter((step) => step.id !== selectedId));
    setNodes((current) => current.filter((node) => node.id !== selectedId));
    setSelectedId(undefined);
  };

  return <div className="orchestration-workbench">
    <section className="orchestration-main panel">
      <div className="panel-head"><span>线性交付旅程</span><small>{draft ? `草稿版本 ${draft.version}` : revision ? `发布版本 ${revision.revision}` : "尚无旅程"}</small></div>
      <div className="orchestration-toolbar">
        {latestDraft && viewMode === "published" && <button onClick={() => setViewMode("draft")}>返回现有草稿</button>}
        {latestDraft && revision && viewMode === "draft" && <button onClick={() => setViewMode("published")}>查看已发布版本</button>}
        {!latestDraft && revision && <button className="primary" onClick={() => clone.mutate()}>克隆为可编辑草稿</button>}
        {draft && <>
          <div className="node-forge" aria-label="新增旅程节点"><button onClick={() => addStep("role")}><Bot size={15}/>添加角色阶段</button><button onClick={() => addStep("code")}><Code2 size={15}/>添加代码交付</button><button onClick={() => addStep("gate")}><ShieldQuestion size={15}/>添加审批关卡</button></div>
          <span className="toolbar-separator"/><button onClick={undo} disabled={!history.current.length}><Undo2 size={15}/>撤销布局</button><button onClick={redo} disabled={!future.current.length}><Redo2 size={15}/>重做布局</button><span className="toolbar-separator"/><button onClick={() => save.mutate()} disabled={steps.length === 0}><Save size={15}/>保存语义与顺序</button><button onClick={() => validate.mutate()}><CheckCircle2 size={15}/>ACWM 校验</button><button className="primary" disabled={draft.validation_status !== "valid"} onClick={() => publish.mutate()}>发布不可变版本</button>
        </>}
      </div>
      <div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={Boolean(draft)} nodesConnectable={false} elementsSelectable onNodeClick={(_, node) => setSelectedId(node.id)} onNodesChange={onNodesChange}><Background gap={24}/><Controls showInteractive={false}/><MiniMap nodeColor={(node) => node.className?.toString().includes("gate") ? "#ff9b61" : "#38a3ff"}/></ReactFlow></div>
      {operationError && <ErrorState error={operationError}/>} 
      {draft?.validation_errors.length ? <div className="validation-errors"><b>发布前必须修复</b>{draft.validation_errors.map((error) => <span key={error}>{error}</span>)}</div> : null}
      <div className="revision-strip"><span>发布版本 SHA-256</span><code>{revision?.fingerprint ?? "尚未生成不可变版本"}</code><span>当前节点</span><code>{steps.length}</code></div>
    </section>
    <aside className="orchestration-inspector panel">
      <div className="panel-head"><span>语义检查器</span><small>{selectedStep ? stepDisplayName(selectedStep) : "请选择节点"}</small></div>
      {selectedStep ? <StepInspector step={selectedStep} editable={Boolean(draft)} bindings={bindings.data ?? []} onChange={updateSelected} onDelete={deleteSelected}/> : <div className="inspector-placeholder"><GitCompareArrows size={24}/><b>选择一个节点</b><span>检查节点类型、执行模式、Capability 和发布语义。</span></div>}
      {draft && revision && <RevisionDelta current={steps} published={parseSteps(revision.definition)}/>}
      {revision && <div className="binding-snapshot"><b>发布绑定快照</b>{Object.entries(revision.binding_snapshot).map(([capability, binding]) => <span key={capability}><code>{capability}</code><small>{String(binding.instance_id ?? "无实例")}</small></span>)}</div>}
    </aside>
  </div>;
}

function RevisionDelta({ current, published }: { current: Step[]; published: Step[] }) {
  const publishedById = new Map(published.map((step) => [step.id, JSON.stringify(step)]));
  const currentById = new Map(current.map((step) => [step.id, JSON.stringify(step)]));
  const added = current.filter((step) => !publishedById.has(step.id)).length;
  const removed = published.filter((step) => !currentById.has(step.id)).length;
  const changed = current.filter((step) => publishedById.has(step.id) && publishedById.get(step.id) !== JSON.stringify(step)).length;
  return <div className="revision-delta"><b>相对已发布版本</b><span>新增 <strong>{added}</strong></span><span>修改 <strong>{changed}</strong></span><span>删除 <strong>{removed}</strong></span></div>;
}

function StepInspector({ step, editable, bindings, onChange, onDelete }: { step: Step; editable: boolean; bindings: Binding[]; onChange: (step: Step) => void; onDelete: () => void }) {
  const boundCapabilities = new Set(bindings.map((binding) => binding.capability_id));
  if (step.kind === "approval_gate") return <div className="step-editor"><StatusBadge value="awaiting_plan_decision"/><h2>{stepDisplayName(step)}</h2><dl><dt>节点 ID</dt><dd><code>{step.id}</code></dd><dt>节点类型</dt><dd>人工审批关卡</dd></dl><label>审批主题<select disabled={!editable} value={step.subject_kind} onChange={(event) => onChange({ ...step, subject_kind: event.target.value as GateStep["subject_kind"] })}><option value="delivery-plan">交付计划</option><option value="candidate-change">候选变更</option></select></label>{editable && <button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除当前节点</button>}</div>;
  const capability = Object.values(step.bindings)[0] ?? "";
  const roleStage = step.workflow_mode === "agentscope.role-turn";
  const options = roleStage ? ["hermes-pm", "hermes-project-admin"] : ["codex-backend"];
  return <div className="step-editor"><StatusBadge value="executing"/><h2>{stepDisplayName(step)}</h2><dl><dt>节点 ID</dt><dd><code>{step.id}</code></dd><dt>执行模式</dt><dd>{roleStage ? "AgentScope 角色回合" : "受控代码交付"}</dd></dl><label>阶段类型<select disabled={!editable} value={step.workflow_mode} onChange={(event) => onChange(changeStageMode(step, event.target.value as StageStep["workflow_mode"]))}><option value="agentscope.role-turn">角色执行阶段</option><option value="code-delivery">代码交付阶段</option></select></label><label>Capability<select disabled={!editable} value={capability} onChange={(event) => onChange(changeCapability(step, event.target.value))}>{options.map((value) => <option key={value} value={value} disabled={!boundCapabilities.has(value)}>{value}{boundCapabilities.has(value) ? " · 已绑定" : " · 未绑定"}</option>)}</select></label>{!boundCapabilities.has(capability) && <div className="repair-callout"><b>Capability 尚未绑定</b><span>先在“智能体实例”中绑定可用实例，再执行 ACWM 校验。</span></div>}{editable && <button className="danger button-icon" onClick={onDelete}><Trash2 size={15}/>删除当前节点</button>}</div>;
}

export function createStep(kind: "role" | "code" | "gate", existing: Step[]): Step {
  const prefix = kind === "gate" ? "approval" : kind === "code" ? "delivery" : "role";
  const id = nextId(prefix, existing.map((step) => step.id));
  if (kind === "gate") return { id, kind: "approval_gate", subject_kind: "delivery-plan" };
  if (kind === "code") return { id, kind: "stage", workflow_mode: "code-delivery", bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" };
  return { id, kind: "stage", workflow_mode: "agentscope.role-turn", bindings: { actor: "hermes-pm" } };
}

export function changeStageMode(step: StageStep, mode: StageStep["workflow_mode"]): StageStep {
  return mode === "code-delivery" ? { ...step, workflow_mode: mode, bindings: { developer: "codex-backend" }, output_validator: "backend-candidate-v1" } : { ...step, workflow_mode: mode, bindings: { actor: "hermes-pm" }, output_validator: undefined };
}

export function changeCapability(step: StageStep, capability: string): StageStep {
  return { ...step, bindings: { [step.workflow_mode === "code-delivery" ? "developer" : "actor"]: capability } };
}

function nextId(prefix: string, ids: string[]) { let index = 1; while (ids.includes(`${prefix}-${index}`)) index += 1; return `${prefix}-${index}`; }
function parseSteps(definition: unknown): Step[] { const parsed = definitionSchema.safeParse(definition); return parsed.success ? parsed.data.steps : []; }
function nextNodePosition(nodes: Node[]): Position { const ordered = orderedNodes(nodes); const last = ordered.at(-1); return { x: last ? last.position.x + 230 : 40, y: ordered.length % 2 ? 150 : 45 }; }
function createNodes(steps: Step[], layout?: Record<string, unknown>): Node[] { return steps.map((step, index) => { const stored = layout?.[step.id]; return createNode(step, isPosition(stored) ? stored : { x: index * 230, y: index % 2 ? 150 : 45 }); }); }
function createNode(step: Step, position: Position): Node { return { id: step.id, position, data: { label: <><small>{step.kind === "approval_gate" ? "审批关卡" : step.workflow_mode === "code-delivery" ? "代码交付" : "角色阶段"}</small><b>{stepDisplayName(step)}</b></> }, className: `flow-node ${step.kind === "approval_gate" ? "gate" : "stage"}` }; }
function stepDisplayName(step: Step) { const translated = journeyStepLabel(step.id); return translated === step.id ? step.id : translated; }
function isPosition(value: unknown): value is Position { return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number"; }
function orderedNodes(nodes: Node[]) { return [...nodes].sort((left, right) => left.position.x - right.position.x || left.position.y - right.position.y); }
function copyNode(node: Node): Node { return { ...node, position: { ...node.position } }; }
