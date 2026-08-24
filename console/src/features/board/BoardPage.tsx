import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DndContext, DragOverlay, PointerSensor, KeyboardSensor, closestCenter, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent, type DragStartEvent } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, X } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { commandLabel } from "../../i18n";
import { useProjectId } from "../../entities/project/api";

type WorkItem = components["schemas"]["WorkItem"];
type BoardColumn = WorkItem["column"];
type WorkCommand = components["schemas"]["WorkItemCommand"]["command"];

const columns: Array<{ id: BoardColumn; label: string; note: string }> = [
  { id: "backlog", label: "待规划", note: "等待机器生成计划" },
  { id: "plan-approval", label: "计划审批", note: "需要人工授权" },
  { id: "executing", label: "执行中", note: "机器受控运行" },
  { id: "candidate-approval", label: "候选审批", note: "检查差异与证据" },
  { id: "completed", label: "已完成", note: "已生成应用回执" },
  { id: "failed-cancelled", label: "失败 / 取消", note: "主分支未被污染" },
];

const commandByMove: Partial<Record<BoardColumn, Partial<Record<BoardColumn, WorkCommand>>>> = {
  "plan-approval": { executing: "approve-plan", "failed-cancelled": "reject-plan" },
  "candidate-approval": { completed: "accept-candidate", "failed-cancelled": "reject-candidate" },
  backlog: { "failed-cancelled": "cancel" },
  executing: { "failed-cancelled": "cancel" },
};

export function resolveDropCommand(item: WorkItem, target: BoardColumn): WorkCommand | undefined {
  const proposed = commandByMove[item.column]?.[target];
  return proposed && item.available_commands.includes(proposed) ? proposed : undefined;
}

type PendingDrop = { item: WorkItem; target: BoardColumn; command: WorkCommand };

export function BoardPage() {
  const projectId = useProjectId();
  const client = useQueryClient();
  const board = useQuery({ queryKey: ["board", projectId], queryFn: () => request<WorkItem[]>(`/v1/board?project_id=${encodeURIComponent(projectId)}`), refetchInterval: 1500 });
  const [active, setActive] = useState<WorkItem>();
  const [pending, setPending] = useState<PendingDrop>();
  const [notice, setNotice] = useState<string>();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const command = useMutation({
    mutationFn: ({ item, command: value }: { item: WorkItem; command: WorkCommand }) => request<WorkItem>(`/v1/work-items/${item.id}/command`, { method: "POST", body: JSON.stringify({ command: value, expected_version: item.version }) }),
    onSuccess: async () => { setPending(undefined); await Promise.all([client.invalidateQueries({ queryKey: ["board", projectId] }), client.invalidateQueries({ queryKey: ["deliveries", projectId] })]); },
  });
  const grouped = useMemo(() => Object.fromEntries(columns.map((column) => [column.id, (board.data ?? []).filter((item) => item.column === column.id)])) as Record<BoardColumn, WorkItem[]>, [board.data]);

  if (board.isLoading) return <LoadingState label="正在从交付事件重建看板投影…"/>;
  if (board.error) return <ErrorState error={board.error} retry={() => board.refetch()}/>;

  const onDragEnd = ({ active: drag, over }: DragEndEvent) => {
    setActive(undefined);
    const item = board.data?.find((candidate) => candidate.id === drag.id);
    const target = over?.id as BoardColumn | undefined;
    if (!item || !target || target === item.column) return;
    const proposed = resolveDropCommand(item, target);
    if (!proposed) {
      setNotice(`不能从“${columns.find((entry) => entry.id === item.column)?.label}”直接移动到“${columns.find((entry) => entry.id === target)?.label}”。状态由交付命令和证据决定。`);
      return;
    }
    setNotice(undefined);
    setPending({ item, target, command: proposed });
  };

  return <>
    <div className="board-toolbar"><div><span className="eyebrow">项目事件投影 · {projectId}</span><b>拖动卡片只发出合法命令</b></div><span>卡片不会被界面直接改成“完成”</span></div>
    {notice && <div className="conflict-banner"><b>非法状态跳转已回弹</b><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice(undefined)}><X size={15}/></button></div>}
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={({ active: drag }: DragStartEvent) => setActive(board.data?.find((item) => item.id === drag.id))} onDragEnd={onDragEnd} onDragCancel={() => setActive(undefined)}>
      <section className="board interactive-board">{columns.map((column) => <BoardLane key={column.id} {...column} items={grouped[column.id]}/>)}</section>
      <DragOverlay>{active && <WorkCard item={active} overlay/>}</DragOverlay>
    </DndContext>
    {pending && <div className="modal-backdrop" role="presentation"><section className="command-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title"><span className="eyebrow">状态命令确认</span><h2 id="command-title">{commandLabel(pending.command)}</h2><p>此操作将对交付 <code>{pending.item.delivery_id}</code> 发出真实命令。目标列只会在后端成功写入状态和事件后更新。</p><div className="decision-row"><button className="primary" disabled={command.isPending} onClick={() => command.mutate({ item: pending.item, command: pending.command })}>确认发出命令</button><button className="secondary" disabled={command.isPending} onClick={() => setPending(undefined)}>返回看板</button></div>{command.error && <ErrorState error={command.error}/>}</section></div>}
  </>;
}

function BoardLane({ id, label, note, items }: { id: BoardColumn; label: string; note: string; items: WorkItem[] }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return <div ref={setNodeRef} className={`board-column ${isOver ? "drop-target" : ""}`}><div className="column-head"><div><b>{label}</b><small>{note}</small></div><span>{items.length}</span></div>{items.length === 0 ? <div className="lane-empty">拖放到这里以发出合法命令</div> : items.map((item) => <WorkCard key={item.id} item={item}/>)}</div>;
}

function WorkCard({ item, overlay = false }: { item: WorkItem; overlay?: boolean }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: item.id, disabled: item.available_commands.length === 0 });
  return <article ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform) }} className={`work-card ${overlay ? "overlay" : ""} ${isDragging ? "dragging" : ""}`}><div className="card-top"><StatusBadge value={item.column}/><button className="drag-handle" aria-label={`拖动交付 ${item.id}`} {...listeners} {...attributes} disabled={item.available_commands.length === 0}><GripVertical size={16}/></button></div><small>交付 {item.delivery_id.slice(0, 8)}</small><h3>{item.title}</h3><p>{item.acceptance_ids.join(" · ") || "等待任务合同"}</p><div className="command-hints">{item.available_commands.length ? item.available_commands.map((value) => <span key={value}>{commandLabel(value)}</span>) : <span>当前无可用命令</span>}</div></article>;
}
