import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DndContext, DragOverlay, PointerSensor, KeyboardSensor, closestCenter, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent, type DragStartEvent } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { Button, Input, Select } from "antd";
import { Eye, GripVertical, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { ConfirmDialog } from "../../shared/feedback/ConfirmDialog";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { commandLabel } from "../../i18n";
import { assertProjectScope, projectPath, useProjectId } from "../../entities/project/api";

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

export function filterWorkItems(items: WorkItem[], query: string, column?: BoardColumn | "all") {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  return items.filter((item) => (column === undefined || column === "all" || item.column === column) && (!normalized || [item.title, item.delivery_id, ...item.acceptance_ids].some((value) => value.toLocaleLowerCase("zh-CN").includes(normalized))));
}

type PendingDrop = { item: WorkItem; target: BoardColumn; command: WorkCommand };

export function BoardPage() {
  const projectId = useProjectId();
  const client = useQueryClient();
  const board = useQuery({ queryKey: ["board", projectId], queryFn: async ({ signal }) => assertProjectScope(projectId, await request<WorkItem[]>(`/v1/board?project_id=${encodeURIComponent(projectId)}`, { signal }), "看板任务"), refetchInterval: 1500 });
  const [active, setActive] = useState<WorkItem>();
  const [pending, setPending] = useState<PendingDrop>();
  const [notice, setNotice] = useState<string>();
  const [query, setQuery] = useState("");
  const [columnFilter, setColumnFilter] = useState<BoardColumn | "all">("all");
  const [selected, setSelected] = useState<WorkItem>();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const command = useMutation({
    mutationFn: ({ item, command: value }: { item: WorkItem; command: WorkCommand }) => request<WorkItem>(`/v1/work-items/${item.id}/command`, { method: "POST", body: JSON.stringify({ command: value, expected_version: item.version }) }),
    onSuccess: async () => { setPending(undefined); await Promise.all([client.invalidateQueries({ queryKey: ["board", projectId] }), client.invalidateQueries({ queryKey: ["deliveries", projectId] })]); },
  });
  const visibleItems = useMemo(() => filterWorkItems(board.data ?? [], query, columnFilter), [board.data, columnFilter, query]);
  const grouped = useMemo(() => Object.fromEntries(columns.map((column) => [column.id, visibleItems.filter((item) => item.column === column.id)])) as Record<BoardColumn, WorkItem[]>, [visibleItems]);

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
    <div className="board-toolbar"><div><span className="eyebrow">项目事件投影 · {projectId}</span><b>拖动卡片只发出合法命令</b></div><div className="board-filters"><Input aria-label="搜索看板任务" prefix={<Search size={15}/>} placeholder="搜索任务、交付或验收 ID" value={query} onChange={(event) => setQuery(event.target.value)}/><label>状态列<Select aria-label="筛选状态列" value={columnFilter} onChange={setColumnFilter} options={[{ value: "all", label: "全部状态" }, ...columns.map((column) => ({ value: column.id, label: column.label }))]}/></label><span>{visibleItems.length}/{board.data?.length ?? 0} 个任务</span></div></div>
    {notice && <div className="conflict-banner"><b>非法状态跳转已回弹</b><span>{notice}</span><Button type="text" aria-label="关闭提示" icon={<X size={15}/>} onClick={() => setNotice(undefined)}/></div>}
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={({ active: drag }: DragStartEvent) => setActive(board.data?.find((item) => item.id === drag.id))} onDragEnd={onDragEnd} onDragCancel={() => setActive(undefined)}>
      <section className="board interactive-board">{columns.map((column) => <BoardLane key={column.id} {...column} items={grouped[column.id]} onOpen={setSelected}/>)}</section>
      <DragOverlay>{active && <WorkCard item={active} overlay/>}</DragOverlay>
    </DndContext>
    <ConfirmDialog open={Boolean(pending)} title={pending ? commandLabel(pending.command) : "确认看板命令"} detail={`此操作将对交付 ${pending?.item.delivery_id ?? ""} 发出真实命令。卡片只会在后端成功写入状态、事件和对应证据后进入目标列。`} confirmLabel="确认发出命令" cancelLabel="返回看板" tone={pending && ["reject-plan", "reject-candidate", "cancel"].includes(pending.command) ? "danger" : "warning"} pending={command.isPending} onCancel={() => setPending(undefined)} onConfirm={() => { if (pending) command.mutate({ item: pending.item, command: pending.command }); }}/>
    {command.error && <ErrorState error={command.error}/>}
    {selected && <aside className="work-item-drawer" role="dialog" aria-modal="false" aria-labelledby="work-item-title"><header><div><span className="eyebrow">项目任务详情</span><h2 id="work-item-title">{selected.title}</h2></div><Button type="text" aria-label="关闭任务详情" icon={<X size={16}/>} onClick={() => setSelected(undefined)}/></header><dl><dt>交付 ID</dt><dd><code>{selected.delivery_id}</code></dd><dt>当前状态</dt><dd><StatusBadge value={selected.column}/></dd><dt>投影版本</dt><dd>v{selected.version}</dd><dt>验收标准</dt><dd>{selected.acceptance_ids.length ? selected.acceptance_ids.join(" · ") : "等待任务合同"}</dd><dt>可用命令</dt><dd>{selected.available_commands.length ? selected.available_commands.map(commandLabel).join("、") : "当前无可用命令"}</dd></dl><Link className="primary drawer-link" to={`${projectPath(projectId, "deliveries")}?delivery_id=${encodeURIComponent(selected.delivery_id)}`}>打开该交付并审查证据</Link></aside>}
  </>;
}

function BoardLane({ id, label, note, items, onOpen }: { id: BoardColumn; label: string; note: string; items: WorkItem[]; onOpen: (item: WorkItem) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return <div ref={setNodeRef} className={`board-column ${isOver ? "drop-target" : ""}`}><div className="column-head"><div><b>{label}</b><small>{note}</small></div><span>{items.length}</span></div>{items.length === 0 ? <div className="lane-empty">当前筛选下没有任务</div> : items.map((item) => <WorkCard key={item.id} item={item} onOpen={() => onOpen(item)}/>)}</div>;
}

function WorkCard({ item, overlay = false, onOpen }: { item: WorkItem; overlay?: boolean; onOpen?: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: item.id, disabled: item.available_commands.length === 0 });
  return <article ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform) }} className={`work-card ${overlay ? "overlay" : ""} ${isDragging ? "dragging" : ""}`}><div className="card-top"><StatusBadge value={item.column}/><div><Button type="text" className="card-detail-button" aria-label={`查看任务 ${item.title}`} icon={<Eye size={15}/>} onClick={onOpen} disabled={!onOpen}/><Button type="text" className="drag-handle" aria-label={`拖动交付 ${item.id}`} icon={<GripVertical size={16}/>} {...listeners} {...attributes} disabled={item.available_commands.length === 0}/></div></div><small>交付 {item.delivery_id.slice(0, 8)}</small><h3>{item.title}</h3><p>{item.acceptance_ids.join(" · ") || "等待任务合同"}</p><div className="command-hints">{item.available_commands.length ? item.available_commands.map((value) => <span key={value}>{commandLabel(value)}</span>) : <span>当前无可用命令</span>}</div></article>;
}
