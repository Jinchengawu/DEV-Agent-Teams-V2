import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Select } from "antd";
import {
  Background,
  Controls,
  MiniMap,
  Position as FlowPosition,
  ReactFlow,
  applyEdgeChanges,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  Bot,
  CheckCircle2,
  Code2,
  GitBranch,
  Maximize2,
  Plus,
  Redo2,
  Save,
  ShieldQuestion,
  Trash2,
  Undo2,
  Workflow,
  X,
} from "lucide-react";
import { z } from "zod";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import {
  appendWorkspaceEdge,
  appendWorkspaceNode,
  applyWorkspaceLayoutChanges,
  assignWorkspaceDeployment,
  createGraphEditingWorkspace,
  deleteWorkspaceEdge,
  deleteWorkspaceNode,
  updateWorkspaceEdgeCondition,
  updateWorkspaceNode,
  type GraphPosition,
  type SemanticGraphEdge,
} from "./GraphEditingWorkspace";
import {
  commitGraphEditingWorkspace,
  commitTransientGraphEditingWorkspace,
  createGraphEditingHistory,
  redoGraphEditingWorkspace,
  replaceGraphEditingWorkspace,
  undoGraphEditingWorkspace,
} from "./GraphEditingHistory";

type Pipeline = components["schemas"]["Pipeline"];
type Draft = components["schemas"]["PipelineDraft"];
type Revision = components["schemas"]["PipelineRevision"];
type Deployment = components["schemas"]["AgentDeployment"];
type Provider = components["schemas"]["ProviderManifestView"];
type Position = GraphPosition;
type SemanticEdge = SemanticGraphEdge;
type StageNode = {
  id: string;
  kind: "stage";
  workflow_mode: "agentscope.role-turn" | "code-delivery";
  bindings: Record<string, string>;
  output_validator?: string | null;
};
type GateNode = { id: string; kind: "approval_gate"; subject_kind: string };
export type LoopNode = {
  id: string;
  kind: "loop";
  policy: {
    exit_condition: string;
    max_iterations: number;
    timeout_seconds: number;
    on_exhausted: "fail" | "continue" | "needs_attention";
  };
  nodes: Array<StageNode | GateNode>;
  edges: SemanticEdge[];
};
export type GraphNode = StageNode | GateNode | LoopNode;

type ViewportNode = { id: string; position: { x: number; y: number } };

export function graphViewportIdentity(
  draftId: string | undefined,
  draftVersion: number | undefined,
  nodes: ViewportNode[],
): string {
  const geometry = nodes
    .map((node) => `${node.id}:${node.position.x}:${node.position.y}`)
    .sort()
    .join("|");
  return `${draftId ?? "no-draft"}:${draftVersion ?? 0}:${geometry}`;
}

function hasVisibleFlowNode(container: HTMLElement): boolean {
  const viewport = container.getBoundingClientRect();
  return Array.from(container.querySelectorAll<HTMLElement>(".react-flow__node")).some(
    (node) => {
      const bounds = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      return (
        style.visibility === "visible" &&
        style.display !== "none" &&
        Number(style.opacity) > 0 &&
        bounds.width > 0 &&
        bounds.height > 0 &&
        bounds.right > viewport.left &&
        bounds.left < viewport.right &&
        bounds.bottom > viewport.top &&
        bounds.top < viewport.bottom
      );
    },
  );
}

function graphNodeKindLabel(node: GraphNode): string {
  if (node.kind === "stage") return "执行阶段";
  if (node.kind === "approval_gate") return "审批关卡";
  return "有限循环";
}

const stageSchema = z.object({
  id: z.string(),
  kind: z.literal("stage"),
  workflow_mode: z.enum(["agentscope.role-turn", "code-delivery"]),
  bindings: z.record(z.string(), z.string()),
  output_validator: z.string().nullable().optional(),
});
const gateSchema = z.object({
  id: z.string(),
  kind: z.literal("approval_gate"),
  subject_kind: z.string(),
});
const edgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  condition: z.string().nullable().optional(),
});
const loopSchema = z.object({
  id: z.string(),
  kind: z.literal("loop"),
  policy: z.object({
    exit_condition: z.string(),
    max_iterations: z.number(),
    timeout_seconds: z.number(),
    on_exhausted: z.enum(["fail", "continue", "needs_attention"]),
  }),
  nodes: z.array(z.union([stageSchema, gateSchema])),
  edges: z.array(edgeSchema),
});
const definitionSchema = z.object({
  id: z.string(),
  version: z.string(),
  nodes: z.array(z.union([stageSchema, gateSchema, loopSchema])),
  edges: z.array(edgeSchema),
});

export function OrchestrationPage() {
  const client = useQueryClient();
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => request<Pipeline[]>("/v1/pipelines"),
  });
  const deployments = useQuery({
    queryKey: ["agent-deployments"],
    queryFn: () => request<Deployment[]>("/v1/agent-deployments"),
  });
  const providers = useQuery({
    queryKey: ["provider-manifests"],
    queryFn: () => request<Provider[]>("/v1/provider-manifests"),
  });
  const [selectedPipelineId, setSelectedPipelineId] = useState<string>();
  const selectedPipeline =
    pipelines.data?.find((item) => item.id === selectedPipelineId) ??
    pipelines.data?.[0];
  useEffect(() => {
    if (!selectedPipelineId && pipelines.data?.[0])
      setSelectedPipelineId(pipelines.data[0].id);
  }, [pipelines.data, selectedPipelineId]);
  const drafts = useQuery({
    queryKey: ["pipeline-drafts", selectedPipeline?.id],
    enabled: Boolean(selectedPipeline),
    queryFn: () =>
      request<Draft[]>(`/v1/pipelines/${selectedPipeline?.id}/drafts`),
  });
  const draft = drafts.data?.[0];
  const revision = useQuery({
    queryKey: [
      "pipeline-revision",
      selectedPipeline?.id,
      selectedPipeline?.active_revision,
    ],
    enabled: Boolean(selectedPipeline?.active_revision),
    queryFn: () =>
      request<Revision>(
        `/v1/pipelines/${selectedPipeline?.id}/revisions/${selectedPipeline?.active_revision}`,
      ),
  });
  const parsed = useMemo(
    () => parseDefinition(draft?.definition),
    [draft?.definition],
  );
  const savedWorkspace = useMemo(() => createGraphEditingWorkspace(parsed.nodes, parsed.edges, draft?.layout, draft?.agent_assignments), [draft?.agent_assignments, draft?.layout, parsed.edges, parsed.nodes]);
  const [history, setHistory] = useState(() => createGraphEditingHistory(savedWorkspace));
  const workspace = history.present;
  const isDirty = JSON.stringify(workspace) !== JSON.stringify(savedWorkspace);
  const dragOrigin = useRef<typeof workspace | undefined>(undefined);
  const editWorkspace = (update: (workspace: typeof history.present) => typeof history.present) => setHistory((current) => commitGraphEditingWorkspace(current, update(current.present)));
  const replaceWorkspace = (update: (workspace: typeof history.present) => typeof history.present) => setHistory((current) => replaceGraphEditingWorkspace(current, update(current.present)));
  const undoWorkspace = () => { setHistory((current) => undoGraphEditingWorkspace(current)); setSelectedNodeId(undefined); setSelectedEdgeId(undefined); };
  const redoWorkspace = () => { setHistory((current) => redoGraphEditingWorkspace(current)); setSelectedNodeId(undefined); setSelectedEdgeId(undefined); };
  const nodes = useMemo(
    () => createFlowNodes(workspace.nodes, workspace.layout),
    [workspace.layout, workspace.nodes],
  );
  const savedFlowNodes = useMemo(
    () => createFlowNodes(savedWorkspace.nodes, savedWorkspace.layout),
    [savedWorkspace.layout, savedWorkspace.nodes],
  );
  const nodesRef = useRef(nodes);
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);
  const edges = useMemo(
    () => createFlowEdges(workspace.edges),
    [workspace.edges],
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [connectionError, setConnectionError] = useState<string>();
  const [flowInstance, setFlowInstance] =
    useState<ReactFlowInstance<Node, Edge>>();
  const flowContainer = useRef<HTMLDivElement>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    kind: "node" | "edge";
    id: string;
  }>();
  const [pendingRelease, setPendingRelease] = useState<
    "publish" | "activate"
  >();

  useEffect(() => {
    const next = parseDefinition(draft?.definition);
    setHistory(createGraphEditingHistory(createGraphEditingWorkspace(
        next.nodes,
        next.edges,
        draft?.layout,
        draft?.agent_assignments,
      )));
    setSelectedNodeId(undefined);
    setSelectedEdgeId(undefined);
  }, [draft?.id, draft?.version]);
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redoWorkspace() : undoWorkspace(); }
      else if (event.key.toLowerCase() === "y") { event.preventDefault(); redoWorkspace(); }
    };
    document.addEventListener("keydown", handleShortcut);
    return () => document.removeEventListener("keydown", handleShortcut);
  }, []);
  const viewportIdentity = useMemo(
    () => graphViewportIdentity(draft?.id, draft?.version, savedFlowNodes),
    [draft?.id, draft?.version, savedFlowNodes],
  );
  const fitAllNodes = useCallback(() => {
    const currentNodes = nodesRef.current;
    if (!flowInstance || currentNodes.length === 0) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        void flowInstance.fitView({
          nodes: nodesRef.current.map((node) => ({ id: node.id })),
          padding: 0.18,
          duration: 180,
          minZoom: 0.1,
          maxZoom: 1.2,
        });
      });
    });
  }, [flowInstance]);
  useEffect(() => {
    fitAllNodes();
  }, [fitAllNodes, viewportIdentity]);
  useEffect(() => {
    const container = flowContainer.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (!hasVisibleFlowNode(container)) fitAllNodes();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [fitAllNodes]);

  const createPipeline = useMutation({
    mutationFn: (value: { id: string; name: string }) =>
      request<{ pipeline: Pipeline; draft: Draft }>("/v1/pipelines", {
        method: "POST",
        body: JSON.stringify({
          id: value.id,
          name: value.name,
          description: "",
          definition: { id: value.id, version: "4.0.0", nodes: [], edges: [] },
          layout: {},
          input_schema: {},
          agent_assignments: {},
        }),
      }),
    onSuccess: async (created) => {
      await client.invalidateQueries({ queryKey: ["pipelines"] });
      setSelectedPipelineId(created.pipeline.id);
    },
  });
  const save = useMutation({
    mutationFn: () =>
      request<Draft>(`/v1/pipeline-drafts/${draft?.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: draft?.version,
          definition: {
            id: selectedPipeline?.id,
            version: "4.0.0",
            nodes: workspace.nodes,
            edges: workspace.edges,
          },
          layout: workspace.layout,
          agent_assignments: workspace.assignments,
        }),
      }),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: ["pipeline-drafts", selectedPipeline?.id],
      }),
  });
  const validate = useMutation({
    mutationFn: () =>
      request<Draft>(`/v1/pipeline-drafts/${draft?.id}/validate`, {
        method: "POST",
        body: JSON.stringify({ expected_version: draft?.version }),
      }),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: ["pipeline-drafts", selectedPipeline?.id],
      }),
  });
  const publish = useMutation({
    mutationFn: () =>
      request<Revision>(`/v1/pipeline-drafts/${draft?.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ expected_version: draft?.version }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }),
  });
  const activate = useMutation({
    mutationFn: (target: Revision) =>
      request<Pipeline>(`/v1/pipelines/${target.pipeline_id}/activate`, {
        method: "POST",
        body: JSON.stringify({
          revision: target.revision,
          expected_version: selectedPipeline?.version,
        }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["pipelines"] }),
  });

  if (pipelines.isLoading || deployments.isLoading || providers.isLoading)
    return <LoadingState label="正在读取流水线、Agent 部署与 Provider…" />;
  if (pipelines.error || deployments.error || providers.error)
    return (
      <ErrorState
        error={(pipelines.error || deployments.error || providers.error)!}
      />
    );

  const addNode = (kind: "role" | "code" | "gate" | "loop") => {
    if (!draft) return;
    const semantic = createGraphNode(kind, workspace.nodes);
    editWorkspace((current) =>
      appendWorkspaceNode(current, semantic, nextPosition(nodes)),
    );
    setSelectedNodeId(semantic.id);
    setSelectedEdgeId(undefined);
  };
  const connect = (connection: Connection) => {
    if (!connection.source || !connection.target) return;
    if (
      !canConnectGraph(
        workspace.nodes,
        workspace.edges,
        connection.source,
        connection.target,
      )
    ) {
      setConnectionError("该连线会形成自环、重复依赖或 DAG 环路，已拒绝保存。");
      return;
    }
    setConnectionError(undefined);
    editWorkspace((current) =>
      appendWorkspaceEdge(current, {
        source: connection.source!,
        target: connection.target!,
      }),
    );
  };
  const updateSelected = (updated: GraphNode) => {
    editWorkspace((current) => updateWorkspaceNode(current, updated));
  };
  const removeSelected = () => {
    if (!selectedNodeId) return;
    editWorkspace((current) => deleteWorkspaceNode(current, selectedNodeId));
    setSelectedNodeId(undefined);
  };
  const selectedNode = workspace.nodes.find(
    (item) => item.id === selectedNodeId,
  );
  const selectedEdge = edges.find((item) => item.id === selectedEdgeId);
  const operationError =
    createPipeline.error ||
    save.error ||
    validate.error ||
    publish.error ||
    activate.error;

  return (
    <div className="pipeline-orchestration">
      <aside className="pipeline-catalog panel">
        <div className="panel-head">
          <span>流水线目录</span>
          <small>{pipelines.data?.length ?? 0} 条</small>
        </div>
        <PipelineCreator
          pending={createPipeline.isPending}
          onCreate={(id, name) => createPipeline.mutate({ id, name })}
        />
        <div className="pipeline-list">
          {pipelines.data?.map((pipeline) => (
            <Button
              className={pipeline.id === selectedPipeline?.id ? "selected" : ""}
              key={pipeline.id}
              onClick={() => setSelectedPipelineId(pipeline.id)}
            >
              <b>{pipeline.name}</b>
              <code>{pipeline.id}</code>
              <small>
                {pipeline.active_revision
                  ? `活动版本 R${pipeline.active_revision}`
                  : "尚未激活版本"}
              </small>
            </Button>
          ))}
        </div>
      </aside>
      <section className="orchestration-main panel">
        <div className="panel-head">
          <span>{selectedPipeline?.name ?? "创建第一条流水线"}</span>
          <small>{draft ? `草稿 V${draft.version} · ${draft.validation_status}${isDirty ? " · 有未保存修改" : ""}` : "无草稿"}</small>
        </div>
        {!selectedPipeline ? (
          <div className="state-box">
            <div>
              <Workflow size={24} />
              <b>还没有流水线</b>
              <span>在左侧创建一条流水线后开始 DAG 编排。</span>
            </div>
          </div>
        ) : drafts.isLoading ? (
          <LoadingState label="正在加载流水线草稿…" />
        ) : !draft ? (
          <div className="state-box">
            <b>该流水线没有可编辑草稿</b>
          </div>
        ) : (
          <>
            <div className="orchestration-toolbar">
              <div className="node-forge" aria-label="新增图节点">
                <Button onClick={() => addNode("role")}>
                  <Bot size={15} />
                  角色 Stage
                </Button>
                <Button onClick={() => addNode("code")}>
                  <Code2 size={15} />
                  交付 Stage
                </Button>
                <Button onClick={() => addNode("gate")}>
                  <ShieldQuestion size={15} />
                  审批 Gate
                </Button>
                <Button onClick={() => addNode("loop")}>
                  <GitBranch size={15} />
                  有限 LOOP
                </Button>
              </div>
              <span className="toolbar-separator" />
              <Button aria-label="撤销图修改" disabled={history.past.length === 0} onClick={undoWorkspace} title="撤销（Ctrl/⌘+Z）">
                <Undo2 size={15} />
                撤销
              </Button>
              <Button aria-label="重做图修改" disabled={history.future.length === 0} onClick={redoWorkspace} title="重做（Ctrl/⌘+Shift+Z）">
                <Redo2 size={15} />
                重做
              </Button>
              <Button aria-label="定位全部节点" disabled={nodes.length === 0} onClick={fitAllNodes}>
                <Maximize2 size={15} />
                定位全部节点
              </Button>
              <Button disabled={!isDirty || save.isPending} onClick={() => save.mutate()}>
                <Save size={15} />
                保存图与布局
              </Button>
              <Button disabled={isDirty || validate.isPending} onClick={() => validate.mutate()}>
                <CheckCircle2 size={15} />
                ACWM 图校验
              </Button>
              <Button
                type="primary"
                disabled={isDirty || draft.validation_status !== "valid" || publish.isPending}
                onClick={() => setPendingRelease("publish")}
              >
                发布不可变版本
              </Button>
              {publish.data && (
                <Button onClick={() => setPendingRelease("activate")}>
                  激活 R{publish.data.revision}
                </Button>
              )}
            </div>
            <label className="node-jump">
              <span>选择主图节点</span>
              <Select
                aria-label="选择主图节点"
                value={selectedNodeId}
                placeholder="从列表定位并编辑节点"
                allowClear
                onChange={(value) => {
                  const nodeId = value || undefined;
                  setSelectedNodeId(nodeId);
                  setSelectedEdgeId(undefined);
                  if (nodeId && flowInstance) {
                    void flowInstance.fitView({
                      nodes: [{ id: nodeId }],
                      padding: 0.65,
                      duration: 180,
                      maxZoom: 1.1,
                    });
                  }
                }}
                options={workspace.nodes.map((node) => ({ value: node.id, label: `${graphNodeKindLabel(node)} · ${node.id}` }))}
              />
            </label>
            <DependencyCreator
              label="主图"
              nodes={workspace.nodes}
              edges={workspace.edges}
              onAdd={(edge) => {
                const semantic = semanticEdges([edge])[0];
                if (semantic)
                  editWorkspace((current) =>
                    appendWorkspaceEdge(current, semantic),
                  );
                setSelectedEdgeId(edge.id);
                setSelectedNodeId(undefined);
              }}
            />
            <div className="flow" ref={flowContainer}>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onInit={setFlowInstance}
                nodesDraggable
                nodesConnectable
                onNodeClick={(_, node) => {
                  setSelectedNodeId(node.id);
                  setSelectedEdgeId(undefined);
                }}
                onEdgeClick={(_, edge) => {
                  setSelectedEdgeId(edge.id);
                  setSelectedNodeId(undefined);
                }}
                onNodeDragStart={() => { dragOrigin.current = workspace; }}
                onNodeDragStop={() => {
                  const origin = dragOrigin.current;
                  if (origin) setHistory((current) => commitTransientGraphEditingWorkspace(current, origin));
                  dragOrigin.current = undefined;
                }}
                onNodesChange={(changes) =>
                  replaceWorkspace((current) =>
                    applyWorkspaceLayoutChanges(
                      current,
                      changes.filter((change) => change.type !== "remove"),
                    ),
                  )
                }
                onConnect={connect}
                deleteKeyCode={null}
              >
                <Background gap={24} />
                <Controls />
                <MiniMap
                  nodeColor={(node) =>
                    node.className?.toString().includes("loop")
                      ? "#b875ff"
                      : node.className?.toString().includes("gate")
                        ? "#ff9b61"
                        : "#38a3ff"
                  }
                />
              </ReactFlow>
            </div>
            {connectionError && (
              <div className="validation-errors">
                <b>连线被拒绝</b>
                <span>{connectionError}</span>
              </div>
            )}
            {draft.validation_errors.length > 0 && (
              <div className="validation-errors">
                <b>ACWM 校验失败</b>
                {draft.validation_errors.map((error) => (
                  <span key={error}>{error}</span>
                ))}
              </div>
            )}
            <div className="revision-strip">
              <span>节点</span>
              <code>{workspace.nodes.length}</code>
              <span>语义边</span>
              <code>{workspace.edges.length}</code>
              <span>活动图指纹</span>
              <code>{revision.data?.fingerprint ?? "尚无活动版本"}</code>
            </div>
            {revision.data && (
              <PublishedBindingSnapshot revision={revision.data} />
            )}
          </>
        )}
        {operationError && <ErrorState error={operationError} />}{" "}
      </section>
      <aside className="orchestration-inspector panel">
        <div className="panel-head">
          <span>图语义检查器</span>
          <small>
            {selectedNode?.id ?? selectedEdge?.id ?? "请选择节点或边"}
          </small>
        </div>
        {selectedNode ? (
          <GraphNodeInspector
            node={selectedNode}
            onChange={updateSelected}
            onDelete={() =>
              setPendingDelete({ kind: "node", id: selectedNode.id })
            }
            assignments={workspace.assignments}
            deployments={deployments.data ?? []}
            providers={providers.data ?? []}
            onAssignment={(site, deploymentId) =>
              editWorkspace((current) =>
                assignWorkspaceDeployment(current, site, deploymentId),
              )
            }
          />
        ) : selectedEdge ? (
          <GraphEdgeInspector
            edge={selectedEdge}
            onConditionChange={(condition) =>
              editWorkspace((current) =>
                updateWorkspaceEdgeCondition(
                  current,
                  selectedEdge.source,
                  selectedEdge.target,
                  condition,
                ),
              )
            }
            onDelete={() =>
              setPendingDelete({ kind: "edge", id: selectedEdge.id })
            }
          />
        ) : (
          <div className="inspector-placeholder">
            <GitBranch size={24} />
            <b>选择节点或连线</b>
            <span>节点坐标只控制布局；连线与条件共同定义执行依赖。</span>
          </div>
        )}
      </aside>
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title={
          pendingDelete?.kind === "node"
            ? `删除节点“${pendingDelete.id}”`
            : `删除连线“${pendingDelete?.id ?? ""}”`
        }
        detail={
          pendingDelete?.kind === "node"
            ? "该节点、所有关联依赖边以及对应的 Agent Assignment 将同时从草稿中移除。保存前仍可通过重新加载草稿放弃本次修改。"
            : "该依赖关系及分支条件将从当前草稿中移除。保存后需要重新建立连线才能恢复。"
        }
        confirmLabel={
          pendingDelete?.kind === "node" ? "确认删除节点" : "确认删除连线"
        }
        tone="danger"
        onCancel={() => setPendingDelete(undefined)}
        onConfirm={() => {
          if (pendingDelete?.kind === "node") removeSelected();
          else if (pendingDelete?.kind === "edge" && selectedEdge) {
            editWorkspace((current) =>
              deleteWorkspaceEdge(
                current,
                selectedEdge.source,
                selectedEdge.target,
              ),
            );
            setSelectedEdgeId(undefined);
          }
          setPendingDelete(undefined);
        }}
      />
      <ConfirmDialog
        open={pendingRelease === "publish"}
        title={`发布“${selectedPipeline?.name ?? "当前流水线"}”不可变版本`}
        detail="发布会冻结当前 DAG、LOOP、Agent Assignment、Provider 与策略快照。已发布 Revision 不能覆盖或修改；后续变更必须产生新 Revision。"
        confirmLabel="确认发布不可变版本"
        pending={publish.isPending}
        onCancel={() => setPendingRelease(undefined)}
        onConfirm={() =>
          publish.mutate(undefined, {
            onSuccess: () => setPendingRelease(undefined),
          })
        }
      />
      <ConfirmDialog
        open={pendingRelease === "activate"}
        title={`激活流水线 R${publish.data?.revision ?? ""}`}
        detail="激活后，新建项目绑定和后续交付可以选择该 Revision；已经启动的交付仍继续使用其冻结版本。"
        confirmLabel={`确认激活 R${publish.data?.revision ?? ""}`}
        pending={activate.isPending}
        onCancel={() => setPendingRelease(undefined)}
        onConfirm={() => {
          if (publish.data)
            activate.mutate(publish.data, {
              onSuccess: () => setPendingRelease(undefined),
            });
        }}
      />
    </div>
  );
}

function PipelineCreator({
  pending,
  onCreate,
}: {
  pending: boolean;
  onCreate: (id: string, name: string) => void;
}) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  return (
    <div className="pipeline-create">
      <Input
        aria-label="流水线 ID"
        placeholder="例如 release-review"
        value={id}
        onChange={(event) => setId(event.target.value)}
      />
      <Input
        aria-label="流水线名称"
        placeholder="中文名称"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <Button
        disabled={pending || !id || !name}
        onClick={() => {
          onCreate(id, name);
          setId("");
          setName("");
        }}
      >
        <Plus size={14} />
        创建流水线
      </Button>
    </div>
  );
}

function DependencyCreator({
  label,
  nodes,
  edges,
  onAdd,
}: {
  label: string;
  nodes: GraphNode[];
  edges: SemanticEdge[];
  onAdd: (edge: Edge) => void;
}) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [condition, setCondition] = useState("");
  const valid = Boolean(
    source && target && canConnectGraph(nodes, edges, source, target),
  );
  return (
    <div className="dependency-creator">
      <b>{label}依赖编辑器</b>
      <label>
        {label}上游节点
        <Select
          aria-label={`${label}上游节点`}
          value={source || undefined}
          placeholder="请选择"
          onChange={setSource}
          options={nodes.map((node) => ({ value: node.id, label: node.id }))}
        />
      </label>
      <label>
        {label}下游节点
        <Select
          aria-label={`${label}下游节点`}
          value={target || undefined}
          placeholder="请选择"
          onChange={setTarget}
          options={nodes.map((node) => ({ value: node.id, label: node.id }))}
        />
      </label>
      <label>
        {label}分支条件
        <Input
          aria-label={`${label}分支条件`}
          placeholder="可选"
          value={condition}
          onChange={(event) => setCondition(event.target.value)}
        />
      </label>
      <Button
        disabled={!valid}
        onClick={() => {
          onAdd(createDependencyEdge(source, target, condition));
          setSource("");
          setTarget("");
          setCondition("");
        }}
      >
        添加依赖边
      </Button>
    </div>
  );
}

function GraphNodeInspector({
  node,
  onChange,
  onDelete,
  assignments,
  deployments,
  providers,
  onAssignment,
}: {
  node: GraphNode;
  onChange: (node: GraphNode) => void;
  onDelete: () => void;
  assignments: Record<string, string>;
  deployments: Deployment[];
  providers: Provider[];
  onAssignment: (site: string, deploymentId: string) => void;
}) {
  if (node.kind === "loop")
    return (
      <LoopNodeInspector
        node={node}
        onChange={onChange}
        onDelete={onDelete}
        assignments={assignments}
        deployments={deployments}
        providers={providers}
        onAssignment={onAssignment}
      />
    );
  if (node.kind === "approval_gate")
    return (
      <div className="step-editor">
        <StatusBadge value="awaiting_plan_decision" />
        <h2>{node.id}</h2>
        <dl>
          <dt>节点类型</dt>
          <dd>全局审批 Gate</dd>
        </dl>
        <label>
          审批主题
          <Select
            aria-label="审批主题"
            value={node.subject_kind}
            onChange={(value) => onChange({ ...node, subject_kind: value })}
            options={[{ value: "delivery-plan", label: "交付计划审批" }, { value: "candidate-change", label: "候选变更审批" }]}
          />
        </label>
        <p className="field-hint">
          审批主题决定产品生成的不可变 Gate Subject 与允许执行的命令。
        </p>
        <Button danger className="button-icon" onClick={onDelete}>
          <Trash2 size={15} />
          删除 Gate
        </Button>
      </div>
    );
  const capability = Object.values(node.bindings)[0] ?? "";
  const site = `${node.id}.${stageSlot(node)}`;
  return (
    <div className="step-editor">
      <StatusBadge value="executing" />
      <h2>{node.id}</h2>
      <dl>
        <dt>节点类型</dt>
        <dd>Workflow Stage</dd>
        <dt>执行模式</dt>
        <dd>{node.workflow_mode}</dd>
      </dl>
      <label>
        Capability
        <Input
          value={capability}
          onChange={(event) => {
            onChange(changeCapability(node, event.target.value));
            onAssignment(site, "");
          }}
        />
      </label>
      <label>
        阶段模式
        <Select
          aria-label="阶段模式"
          value={node.workflow_mode}
          onChange={(value: StageNode["workflow_mode"]) => {
            onChange(
              changeStageMode(node, value),
            );
            onAssignment(site, "");
          }}
          options={[{ value: "agentscope.role-turn", label: "AgentScope 角色执行" }, { value: "code-delivery", label: "受控代码交付" }]}
        />
      </label>
      <DeploymentAssignmentEditor
        site={site}
        capability={capability}
        value={assignments[site]}
        deployments={deployments}
        providers={providers}
        onChange={(deploymentId) => onAssignment(site, deploymentId)}
      />
      <Button danger className="button-icon" onClick={onDelete}>
        <Trash2 size={15} />
        删除 Stage
      </Button>
    </div>
  );
}

function LoopNodeInspector({
  node,
  onChange,
  onDelete,
  assignments,
  deployments,
  providers,
  onAssignment,
}: {
  node: LoopNode;
  onChange: (node: LoopNode) => void;
  onDelete: () => void;
  assignments: Record<string, string>;
  deployments: Deployment[];
  providers: Provider[];
  onAssignment: (site: string, deploymentId: string) => void;
}) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  useEffect(() => {
    if (!workspaceOpen) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const dialog = document.querySelector<HTMLElement>(
      ".loop-workspace-dialog",
    );
    dialog?.querySelector<HTMLElement>("button:not([disabled])")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setWorkspaceOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previous?.focus();
    };
  }, [workspaceOpen]);
  return (
    <div className="step-editor">
      <StatusBadge value="executing" />
      <h2>{node.id}</h2>
      <dl>
        <dt>节点类型</dt>
        <dd>有限 LOOP 子图</dd>
        <dt>内部节点</dt>
        <dd>{node.nodes.length}</dd>
        <dt>依赖边</dt>
        <dd>{node.edges.length}</dd>
      </dl>
      <p className="field-hint">
        循环策略与内部 DAG 在专用工作区编辑，避免在狭窄检查器中误操作。
      </p>
      <Button
        type="primary"
        className="button-icon"
        onClick={() => setWorkspaceOpen(true)}
      >
        <Maximize2 size={15} />
        打开 LOOP 全屏工作区
      </Button>
      <Button danger className="button-icon" onClick={onDelete}>
        <Trash2 size={15} />
        删除 LOOP
      </Button>
      {workspaceOpen && (
        <div className="loop-workspace-backdrop" role="presentation">
          <section
            className="loop-workspace-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="loop-workspace-title"
          >
            <header>
              <div>
                <span className="eyebrow">有界循环工作区</span>
                <h2 id="loop-workspace-title">{node.id}</h2>
                <p>策略定义退出边界；内部节点和依赖边定义每轮执行顺序。关闭后修改仍保留在未保存草稿中。</p>
              </div>
              <Button
                className="button-icon"
                aria-label="关闭 LOOP 工作区并保留草稿修改"
                onClick={() => setWorkspaceOpen(false)}
              >
                <X size={18} />
                关闭并保留修改
              </Button>
            </header>
            <div className="loop-workspace-content">
              <aside>
                <label>
                  退出条件策略
                  <Input
                    value={node.policy.exit_condition}
                    onChange={(event) =>
                      onChange({
                        ...node,
                        policy: {
                          ...node.policy,
                          exit_condition: event.target.value,
                        },
                      })
                    }
                  />
                </label>
                <label>
                  最大轮次
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={node.policy.max_iterations}
                    onChange={(event) =>
                      onChange({
                        ...node,
                        policy: {
                          ...node.policy,
                          max_iterations: Number(event.target.value),
                        },
                      })
                    }
                  />
                </label>
                <label>
                  总超时（秒）
                  <Input
                    type="number"
                    min={1}
                    value={node.policy.timeout_seconds}
                    onChange={(event) =>
                      onChange({
                        ...node,
                        policy: {
                          ...node.policy,
                          timeout_seconds: Number(event.target.value),
                        },
                      })
                    }
                  />
                </label>
                <label>
                  耗尽动作
                  <Select
                    aria-label="耗尽动作"
                    value={node.policy.on_exhausted}
                    onChange={(value: LoopNode["policy"]["on_exhausted"]) =>
                      onChange({
                        ...node,
                        policy: {
                          ...node.policy,
                          on_exhausted: value,
                        },
                      })
                    }
                    options={[{ value: "fail", label: "失败" }, { value: "needs_attention", label: "转人工处理" }, { value: "continue", label: "继续下游" }]}
                  />
                </label>
              </aside>
              <LoopBodyEditor
                loop={node}
                onChange={onChange}
                assignments={assignments}
                deployments={deployments}
                providers={providers}
                onAssignment={onAssignment}
              />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function GraphEdgeInspector({
  edge,
  onConditionChange,
  onDelete,
}: {
  edge: Edge;
  onConditionChange: (condition: string) => void;
  onDelete: () => void;
}) {
  return (
    <div className="step-editor">
      <StatusBadge value="queued" />
      <h2>
        {edge.source} → {edge.target}
      </h2>
      <dl>
        <dt>语义类型</dt>
        <dd>有向依赖边</dd>
      </dl>
      <label>
        分支条件
        <Input
          aria-label="分支条件"
          placeholder="留空表示无条件依赖"
          value={
            typeof edge.data?.condition === "string" ? edge.data.condition : ""
          }
          onChange={(event) => onConditionChange(event.target.value)}
        />
      </label>
      <p className="field-hint">
        条件值必须对应上游节点声明的命名结果；ACWM 发布时会验证。
      </p>
      <Button danger className="button-icon" onClick={onDelete}>
        <Trash2 size={15} />
        删除连线
      </Button>
    </div>
  );
}

function LoopBodyEditor({
  loop,
  onChange,
  assignments,
  deployments,
  providers,
  onAssignment,
}: {
  loop: LoopNode;
  onChange: (node: LoopNode) => void;
  assignments: Record<string, string>;
  deployments: Deployment[];
  providers: Provider[];
  onAssignment: (site: string, deploymentId: string) => void;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [pendingNodeDelete, setPendingNodeDelete] = useState<string>();
  const [bodyNodes, setBodyNodes, onBodyNodesChange] = useNodesState<Node>(
    createLoopFlowNodes(loop.nodes),
  );
  const [bodyEdges, setBodyEdges] = useEdgesState<Edge>(
    createFlowEdges(loop.edges),
  );
  const [flowInstance, setFlowInstance] =
    useState<ReactFlowInstance<Node, Edge>>();
  useEffect(() => {
    setBodyNodes(createLoopFlowNodes(loop.nodes));
    setBodyEdges(createFlowEdges(loop.edges));
  }, [loop.nodes, loop.edges, setBodyEdges, setBodyNodes]);
  useEffect(() => {
    if (flowInstance && bodyNodes.length > 0)
      void flowInstance.fitView({ padding: 0.2, duration: 150 });
  }, [bodyNodes.length, flowInstance]);
  const add = (kind: "role" | "code" | "gate") => {
    const updated = addLoopBodyNode(loop, kind);
    onChange(updated);
    setSelectedNodeId(updated.nodes.at(-1)?.id);
    setSelectedEdgeId(undefined);
  };
  const connect = (connection: Connection) => {
    if (
      !connection.source ||
      !connection.target ||
      !canConnectGraph(
        loop.nodes,
        loop.edges,
        connection.source,
        connection.target,
      )
    )
      return;
    onChange({
      ...loop,
      edges: [
        ...loop.edges,
        { source: connection.source, target: connection.target },
      ],
    });
  };
  const changeEdges = (changes: EdgeChange[]) => {
    setBodyEdges((current) => {
      const updated = applyEdgeChanges(changes, current);
      onChange({ ...loop, edges: semanticEdges(updated) });
      return updated;
    });
  };
  const selectedEdge = bodyEdges.find((edge) => edge.id === selectedEdgeId);
  const selectedBodyNode = loop.nodes.find(
    (node) => node.id === selectedNodeId,
  );
  const selectedSite =
    selectedBodyNode?.kind === "stage"
      ? `${loop.id}/${selectedBodyNode.id}.${stageSlot(selectedBodyNode)}`
      : undefined;
  return (
    <section className="loop-body-editor">
      <div className="loop-body-head">
        <b>LOOP 内部 DAG</b>
        <small>
          {loop.nodes.length} 节点 · {loop.edges.length} 边
        </small>
      </div>
      <div className="node-forge">
        <Button onClick={() => add("role")}>角色 Stage</Button>
        <Button onClick={() => add("code")}>交付 Stage</Button>
      </div>
      <p className="field-hint">
        当前版本只允许循环体执行机器节点；人工审批 Gate 必须放在 LOOP 外部。
      </p>
      <label className="node-jump">
        <span>选择循环体节点</span>
        <Select
          aria-label="选择循环体节点"
          value={selectedNodeId}
          placeholder="从列表定位并编辑节点"
          allowClear
          onChange={(value) => {
            setSelectedNodeId(value || undefined);
            setSelectedEdgeId(undefined);
          }}
          options={loop.nodes.map((node) => ({ value: node.id, label: `${graphNodeKindLabel(node)} · ${node.id}` }))}
        />
      </label>
      <DependencyCreator
        label="循环体"
        nodes={loop.nodes}
        edges={loop.edges}
        onAdd={(edge) =>
          onChange({
            ...loop,
            edges: [...loop.edges, ...semanticEdges([edge])],
          })
        }
      />
      <div className="loop-flow">
        <ReactFlow
          nodes={bodyNodes}
          edges={bodyEdges}
          fitView
          onInit={setFlowInstance}
          onNodesChange={onBodyNodesChange}
          onEdgesChange={changeEdges}
          onConnect={connect}
          onNodeClick={(_, node) => {
            setSelectedNodeId(node.id);
            setSelectedEdgeId(undefined);
          }}
          onEdgeClick={(_, edge) => {
            setSelectedEdgeId(edge.id);
            setSelectedNodeId(undefined);
          }}
          deleteKeyCode={null}
        >
          <Background gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {selectedBodyNode?.kind === "stage" && selectedSite && (
        <DeploymentAssignmentEditor
          site={selectedSite}
          capability={Object.values(selectedBodyNode.bindings)[0] ?? ""}
          value={assignments[selectedSite]}
          deployments={deployments}
          providers={providers}
          onChange={(deploymentId) => onAssignment(selectedSite, deploymentId)}
        />
      )}{" "}
      {selectedNodeId && (
        <Button
          danger
          onClick={() => setPendingNodeDelete(selectedNodeId)}
        >
          删除内部节点 {selectedNodeId}
        </Button>
      )}
      {selectedEdge && (
        <label>
          内部边条件
          <Input
            aria-label="内部边条件"
            value={
              typeof selectedEdge.data?.condition === "string"
                ? selectedEdge.data.condition
                : ""
            }
            onChange={(event) => {
              const updated = setEdgeCondition(
                bodyEdges,
                selectedEdge.id,
                event.target.value,
              );
              setBodyEdges(updated);
              onChange({ ...loop, edges: semanticEdges(updated) });
            }}
          />
        </label>
      )}
      <ConfirmDialog
        open={Boolean(pendingNodeDelete)}
        title={`删除 LOOP 内部节点“${pendingNodeDelete ?? ""}”`}
        detail="该内部节点、所有关联依赖边以及对应的 Agent Assignment 会一起从当前草稿移除。"
        confirmLabel="确认删除内部节点"
        tone="danger"
        onCancel={() => setPendingNodeDelete(undefined)}
        onConfirm={() => {
          if (!pendingNodeDelete) return;
          const target = loop.nodes.find((item) => item.id === pendingNodeDelete);
          if (target?.kind === "stage") onAssignment(`${loop.id}/${target.id}.${stageSlot(target)}`, "");
          onChange(removeLoopBodyNode(loop, pendingNodeDelete));
          setSelectedNodeId(undefined);
          setPendingNodeDelete(undefined);
        }}
      />
    </section>
  );
}

function DeploymentAssignmentEditor({
  site,
  capability,
  value,
  deployments,
  providers,
  onChange,
}: {
  site: string;
  capability: string;
  value?: string;
  deployments: Deployment[];
  providers: Provider[];
  onChange: (deploymentId: string) => void;
}) {
  const compatibleProviderIds = new Set(
    providers
      .filter((provider) =>
        provider.capabilities.some((item) => item.id === capability),
      )
      .map((provider) => provider.id),
  );
  const selectable = deployments.filter((item) =>
    isDeploymentSelectable(item, compatibleProviderIds, capability),
  );
  const unavailable = deployments.filter(
    (item) => !selectable.some((candidate) => candidate.id === item.id),
  );
  const selected = deployments.find((item) => item.id === value);
  return (
    <div className="deployment-assignment">
      <label>
        Agent Deployment
        <Select
          aria-label={`Agent Deployment ${site}`}
          value={value || undefined}
          placeholder="请选择已通过资格检查的部署"
          allowClear
          onChange={(next) => onChange(next ?? "")}
          options={[
            ...(selectable.length > 0 ? [{ label: "可选择", options: selectable.map((item) => ({ value: item.id, label: `${item.name} · ${item.profile_id} → ${item.instance_id}` })) }] : []),
            ...(unavailable.length > 0 ? [{ label: "不可选择", options: unavailable.map((item) => ({ value: item.id, label: `${item.name} · ${deploymentUnavailableReason(item, compatibleProviderIds, capability)}`, disabled: true })) }] : []),
          ]}
        />
      </label>
      <dl>
        <dt>Binding Site</dt>
        <dd>
          <code>{site}</code>
        </dd>
        <dt>Capability</dt>
        <dd>
          <code>{capability || "未配置"}</code>
        </dd>
        {selected && (
          <>
            <dt>冻结来源</dt>
            <dd>
              {selected.profile_id} R{selected.profile_revision} ·{" "}
              {selected.provider_id}
            </dd>
          </>
        )}
      </dl>
      {selectable.length === 0 && (
        <p className="field-hint">
          没有兼容且已启用的 Deployment。请先到“智能体实例 → Agent
          部署”完成资格检查。
        </p>
      )}
    </div>
  );
}

function deploymentUnavailableReason(
  item: Deployment,
  compatibleProviderIds: Set<string>,
  capability: string,
): string {
  if (
    !item.capability_requirements.some(
      (requirement) => requirement.id === capability,
    )
  )
    return "Agent Profile 未声明当前 Capability";
  if (!compatibleProviderIds.has(item.provider_id))
    return "Provider 不支持当前 Capability";
  if (item.qualification_status !== "qualified") return "资格检查未通过";
  if (!item.enabled) return "部署未启用";
  return "当前不可用";
}
export function isDeploymentSelectable(
  item: Deployment,
  compatibleProviderIds: Set<string>,
  capability: string,
): boolean {
  return (
    item.enabled &&
    item.qualification_status === "qualified" &&
    compatibleProviderIds.has(item.provider_id) &&
    item.capability_requirements.some(
      (requirement) => requirement.id === capability,
    )
  );
}
function stageSlot(node: StageNode): string {
  return (
    Object.keys(node.bindings)[0] ??
    (node.workflow_mode === "code-delivery" ? "developer" : "actor")
  );
}

function PublishedBindingSnapshot({ revision }: { revision: Revision }) {
  const bindings = Object.entries(revision.resolved_provider_bindings ?? {});
  return (
    <section className="binding-snapshot">
      <b>已发布执行快照 · {revision.binding_model}</b>
      {bindings.length === 0 ? (
        <p className="field-hint">
          该活动版本使用旧版全局绑定；仅用于历史兼容。
        </p>
      ) : (
        bindings.map(([site, raw]) => {
          const deployment = isRecord(raw.deployment) ? raw.deployment : {};
          const binding = isRecord(raw.binding) ? raw.binding : {};
          return (
            <span key={site}>
              <code>{site}</code>
              <small>
                {stringValue(deployment.profile_id)} →{" "}
                {stringValue(deployment.instance_id)} ·{" "}
                {stringValue(binding.binding_fingerprint).slice(0, 12)}
              </small>
            </span>
          );
        })
      )}
    </section>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "未知";
}

export function createGraphNode(
  kind: "role" | "code" | "gate" | "loop",
  existing: GraphNode[],
): GraphNode {
  const prefix = kind === "role" ? "stage" : kind;
  const id = nextId(
    prefix,
    existing.map((item) => item.id),
  );
  if (kind === "gate") {
    const gateCount = existing.filter(
      (item) => item.kind === "approval_gate",
    ).length;
    return {
      id,
      kind: "approval_gate",
      subject_kind: gateCount === 0 ? "delivery-plan" : "candidate-change",
    };
  }
  if (kind === "loop")
    return {
      id,
      kind: "loop",
      policy: {
        exit_condition: "machine-tests-passed",
        max_iterations: 3,
        timeout_seconds: 300,
        on_exhausted: "fail",
      },
      nodes: [
        {
          id: `${id}-work`,
          kind: "stage",
          workflow_mode: "code-delivery",
          bindings: { developer: "codex-backend" },
          output_validator: "backend-candidate-v1",
        },
      ],
      edges: [],
    };
  if (kind === "code")
    return {
      id,
      kind: "stage",
      workflow_mode: "code-delivery",
      bindings: { developer: "codex-backend" },
      output_validator: "backend-candidate-v1",
    };
  return {
    id,
    kind: "stage",
    workflow_mode: "agentscope.role-turn",
    bindings: { actor: "hermes-pm" },
  };
}
export function canConnectGraph(
  nodes: GraphNode[],
  edges: SemanticEdge[],
  source: string,
  target: string,
): boolean {
  if (
    source === target ||
    !nodes.some((node) => node.id === source) ||
    !nodes.some((node) => node.id === target)
  )
    return false;
  if (edges.some((edge) => edge.source === source && edge.target === target))
    return false;
  const adjacency = new Map<string, string[]>();
  for (const edge of [...edges, { source, target }])
    adjacency.set(edge.source, [
      ...(adjacency.get(edge.source) ?? []),
      edge.target,
    ]);
  const visit = (current: string, seen: Set<string>): boolean => {
    if (current === source) return true;
    if (seen.has(current)) return false;
    seen.add(current);
    return (adjacency.get(current) ?? []).some((next) => visit(next, seen));
  };
  return !visit(target, new Set());
}
export function changeStageMode(
  node: StageNode,
  mode: StageNode["workflow_mode"],
): StageNode {
  return mode === "code-delivery"
    ? {
        ...node,
        workflow_mode: mode,
        bindings: { developer: "codex-backend" },
        output_validator: "backend-candidate-v1",
      }
    : {
        ...node,
        workflow_mode: mode,
        bindings: { actor: "hermes-pm" },
        output_validator: undefined,
      };
}
export function changeCapability(
  node: StageNode,
  capability: string,
): StageNode {
  return {
    ...node,
    bindings: {
      [node.workflow_mode === "code-delivery" ? "developer" : "actor"]:
        capability,
    },
  };
}
export function setEdgeCondition(
  edges: Edge[],
  edgeId: string,
  condition: string,
): Edge[] {
  const normalized = condition.trim() || undefined;
  return edges.map((edge) =>
    edge.id === edgeId
      ? {
          ...edge,
          data: { ...edge.data, condition: normalized },
          label: normalized,
        }
      : edge,
  );
}
export function createDependencyEdge(
  source: string,
  target: string,
  condition: string,
): Edge {
  const normalized = condition.trim() || undefined;
  return {
    id: `edge-${source}-${target}`,
    source,
    target,
    type: "smoothstep",
    data: { condition: normalized },
    label: normalized,
  };
}
export function addLoopBodyNode(
  loop: LoopNode,
  kind: "role" | "code" | "gate",
): LoopNode {
  const created = createGraphNode(kind, loop.nodes);
  if (created.kind === "loop") return loop;
  return { ...loop, nodes: [...loop.nodes, created] };
}
export function removeLoopBodyNode(loop: LoopNode, nodeId: string): LoopNode {
  return {
    ...loop,
    nodes: loop.nodes.filter((node) => node.id !== nodeId),
    edges: loop.edges.filter(
      (edge) => edge.source !== nodeId && edge.target !== nodeId,
    ),
  };
}
export function parseDefinition(value: unknown): {
  id: string;
  version: string;
  nodes: GraphNode[];
  edges: SemanticEdge[];
} {
  const parsed = definitionSchema.safeParse(value);
  return parsed.success
    ? parsed.data
    : { id: "", version: "4.0.0", nodes: [], edges: [] };
}
function semanticEdges(edges: Edge[]): SemanticEdge[] {
  return edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    condition:
      typeof edge.data?.condition === "string"
        ? edge.data.condition
        : undefined,
  }));
}
function createFlowEdges(edges: SemanticEdge[]): Edge[] {
  return edges.map((edge) => ({
    id: `edge-${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    data: { condition: edge.condition },
    label: edge.condition || undefined,
  }));
}
function createFlowNodes(
  nodes: GraphNode[],
  layout?: Record<string, unknown>,
): Node[] {
  return nodes.map((node, index) =>
    createFlowNode(
      node,
      isPosition(layout?.[node.id])
        ? (layout[node.id] as Position)
        : { x: index * 230, y: index % 2 ? 170 : 40 },
    ),
  );
}
function createLoopFlowNodes(nodes: Array<StageNode | GateNode>): Node[] {
  return nodes.map((node, index) =>
    createFlowNode(node, { x: index * 145, y: 55 }),
  );
}
export function createFlowNode(node: GraphNode, position: Position): Node {
  const type =
    node.kind === "loop"
      ? "有限 LOOP"
      : node.kind === "approval_gate"
        ? "审批 Gate"
        : node.workflow_mode === "code-delivery"
          ? "代码交付 Stage"
          : "角色 Stage";
  return {
    id: node.id,
    position,
    // `width`/`height` are React Flow's measured dimensions. Pre-populating
    // them prevents the internal node observer from completing handle
    // measurement, so connected edges never enter the render tree. Initial
    // dimensions preserve a stable first paint without impersonating a
    // completed measurement.
    initialWidth: 170,
    initialHeight: 68,
    sourcePosition: FlowPosition.Bottom,
    targetPosition: FlowPosition.Top,
    // The default node renderer owns the visible Handle elements, while these
    // bounds give React Flow deterministic geometry before ResizeObserver has
    // measured them. Without the bounds the semantic edges exist in state but
    // are filtered from the SVG render tree on the first (and, in WebKit,
    // sometimes every) paint.
    handles: [
      {
        id: null,
        type: "target",
        position: FlowPosition.Top,
        x: 80,
        y: -5,
        width: 10,
        height: 10,
      },
      {
        id: null,
        type: "source",
        position: FlowPosition.Bottom,
        x: 80,
        y: 63,
        width: 10,
        height: 10,
      },
    ],
    data: {
      label: (
        <>
          <small>{type}</small>
          <b>{node.id}</b>
        </>
      ),
    },
    className: `flow-node ${node.kind === "loop" ? "loop" : node.kind === "approval_gate" ? "gate" : "stage"}`,
  };
}
function nextPosition(nodes: Node[]): Position {
  const last = nodes.at(-1);
  return {
    x: last ? last.position.x + 220 : 40,
    y: nodes.length % 2 ? 170 : 40,
  };
}
function isPosition(value: unknown): value is Position {
  return (
    typeof value === "object" &&
    value !== null &&
    "x" in value &&
    "y" in value &&
    typeof value.x === "number" &&
    typeof value.y === "number"
  );
}
function nextId(prefix: string, ids: string[]): string {
  let index = 1;
  while (ids.includes(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}
