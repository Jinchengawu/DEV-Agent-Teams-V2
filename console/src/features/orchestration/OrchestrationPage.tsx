import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, MiniMap, ReactFlow, useNodesState, type Edge, type Node, type NodeChange } from "@xyflow/react";
import { CheckCircle2, GitCompareArrows, Redo2, Save, Undo2 } from "lucide-react";
import { z } from "zod";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { journeyStepLabel } from "../../i18n";

type Draft = components["schemas"]["JourneyDraft"];
type Revision = components["schemas"]["JourneyRevision"];
type Position = { x: number; y: number };
type Step = { id: string; kind: "stage" | "approval_gate"; capability_id?: string; [key: string]: unknown };

const definitionSchema = z.object({ steps: z.array(z.object({ id: z.string(), kind: z.enum(["stage", "approval_gate"]), capability_id: z.string().optional() }).passthrough()) }).passthrough();

export function OrchestrationPage() {
  const client = useQueryClient();
  const journeys = useQuery({ queryKey: ["journeys"], queryFn: () => request<Revision[]>("/v1/journeys") });
  const drafts = useQuery({ queryKey: ["journey-drafts"], queryFn: () => request<Draft[]>("/v1/journey-drafts") });
  const revision = journeys.data?.[0];
  const draft = drafts.data?.at(-1);
  const source = draft?.definition ?? revision?.definition;
  const parsed = definitionSchema.safeParse(source);
  const steps: Step[] = parsed.success ? parsed.data.steps : [];
  const [selectedId, setSelectedId] = useState<string>();
  const initialNodes = useMemo(() => createNodes(steps, draft?.layout), [draft?.layout, source]);
  const [nodes, setNodes, onNodesChangeBase] = useNodesState(initialNodes);
  const history = useRef<Node[][]>([]);
  const future = useRef<Node[][]>([]);
  useEffect(() => { setNodes(initialNodes); history.current = []; future.current = []; }, [initialNodes, setNodes]);
  const edges = useMemo<Edge[]>(() => orderedNodes(nodes).slice(1).map((node, index, list) => ({ id: `linear-${index}`, source: index === 0 ? orderedNodes(nodes)[0].id : list[index - 1].id, target: node.id, animated: Boolean(draft), type: "smoothstep" })), [draft, nodes]);

  const clone = useMutation({ mutationFn: () => request<Draft>("/v1/journey-drafts", { method: "POST", body: JSON.stringify({ name: "后端交付旅程草稿", definition: revision?.definition, layout: {} }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }) });
  const save = useMutation({ mutationFn: () => request<Draft>(`/v1/journey-drafts/${draft?.id}`, { method: "PATCH", body: JSON.stringify({ expected_version: draft?.version, definition: { ...source, steps: orderedNodes(nodes).map((node) => steps.find((step) => step.id === node.id)) }, layout: Object.fromEntries(nodes.map((node) => [node.id, node.position])) }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }) });
  const validate = useMutation({ mutationFn: () => request<Draft>(`/v1/journey-drafts/${draft?.id}/validate`, { method: "POST" }), onSuccess: () => client.invalidateQueries({ queryKey: ["journey-drafts"] }) });
  const publish = useMutation({ mutationFn: () => request<Revision>(`/v1/journey-drafts/${draft?.id}/publish`, { method: "POST" }), onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ["journeys"] }), client.invalidateQueries({ queryKey: ["journey-drafts"] })]); } });

  if (journeys.isLoading || drafts.isLoading) return <LoadingState label="正在解析 ACWM 旅程与草稿…"/>;
  if (journeys.error || drafts.error) return <ErrorState error={(journeys.error || drafts.error)!}/>;

  const onNodesChange = (changes: NodeChange[]) => {
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

  return <div className="orchestration-workbench">
    <section className="orchestration-main panel">
      <div className="panel-head"><span>线性交付旅程</span><small>{draft ? `草稿版本 ${draft.version}` : revision ? `发布版本 ${revision.revision}` : "尚无旅程"}</small></div>
      <div className="orchestration-toolbar">
        {!draft && revision && <button className="primary" onClick={() => clone.mutate()}>克隆为可编辑草稿</button>}
        {draft && <><button onClick={undo} disabled={!history.current.length}><Undo2 size={15}/>撤销</button><button onClick={redo} disabled={!future.current.length}><Redo2 size={15}/>重做</button><span className="toolbar-separator"/><button onClick={() => save.mutate()}><Save size={15}/>保存顺序</button><button onClick={() => validate.mutate()}><CheckCircle2 size={15}/>ACWM 校验</button><button className="primary" disabled={draft.validation_status !== "valid"} onClick={() => publish.mutate()}>发布不可变版本</button></>}
      </div>
      <div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={Boolean(draft)} nodesConnectable={false} elementsSelectable onNodeClick={(_, node) => setSelectedId(node.id)} onNodesChange={onNodesChange}><Background gap={24}/><Controls showInteractive={false}/><MiniMap nodeColor={(node) => node.className?.toString().includes("gate") ? "#ff9b61" : "#38a3ff"}/></ReactFlow></div>
      {operationError && <ErrorState error={operationError}/>} 
      {draft?.validation_errors.length ? <div className="validation-errors"><b>发布前必须修复</b>{draft.validation_errors.map((error) => <span key={error}>{error}</span>)}</div> : null}
      <div className="revision-strip"><span>发布版本 SHA-256</span><code>{revision?.fingerprint ?? "尚未生成不可变版本"}</code></div>
    </section>
    <aside className="orchestration-inspector panel">
      <div className="panel-head"><span>节点检查器</span><small>{selectedStep ? journeyStepLabel(selectedStep.id) : "请选择节点"}</small></div>
      {selectedStep ? <><StatusBadge value={selectedStep.kind === "approval_gate" ? "awaiting_plan_decision" : "executing"}/><h2>{journeyStepLabel(selectedStep.id)}</h2><dl><dt>节点 ID</dt><dd><code>{selectedStep.id}</code></dd><dt>节点类型</dt><dd>{selectedStep.kind === "approval_gate" ? "人工审批关卡" : "智能体执行阶段"}</dd><dt>能力绑定</dt><dd>{selectedStep.capability_id ?? "由审批策略控制"}</dd></dl><p className="field-help">画布坐标只影响界面。运行顺序由保存时的横向拓扑排序转换为 ACWM steps。</p></> : <div className="inspector-placeholder"><GitCompareArrows size={24}/><b>选择一个节点</b><span>检查节点类型、能力和发布语义。</span></div>}
      {revision && <div className="binding-snapshot"><b>发布绑定快照</b>{Object.entries(revision.binding_snapshot).map(([capability, binding]) => <span key={capability}><code>{capability}</code><small>{String(binding.instance_id ?? "无实例")}</small></span>)}</div>}
    </aside>
  </div>;
}

function createNodes(steps: Step[], layout?: Record<string, unknown>): Node[] {
  return steps.map((step, index) => { const stored = layout?.[step.id]; const position = isPosition(stored) ? stored : { x: index * 230, y: index % 2 ? 150 : 45 }; return { id: step.id, position, data: { label: <><small>{step.kind === "approval_gate" ? "审批关卡" : "执行阶段"}</small><b>{journeyStepLabel(step.id)}</b></> }, className: `flow-node ${step.kind === "approval_gate" ? "gate" : "stage"}` }; });
}

function isPosition(value: unknown): value is Position { return typeof value === "object" && value !== null && "x" in value && "y" in value && typeof value.x === "number" && typeof value.y === "number"; }
function orderedNodes(nodes: Node[]) { return [...nodes].sort((left, right) => left.position.x - right.position.x); }
function copyNode(node: Node): Node { return { ...node, position: { ...node.position } }; }

