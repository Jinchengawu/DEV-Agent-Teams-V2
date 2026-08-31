import { useEffect, useMemo, useState } from "react";
import { Button, Input, InputNumber, Select } from "antd";
import { Boxes, GitFork, Plus, Save, Send, ShieldCheck, Trash2 } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import {
  type TeamTemplate,
  type TeamTemplateCreate,
  type TeamTemplateDraft,
  useCreateTeamTemplate,
  usePatchTeamTemplateDraft,
  usePublishTeamTemplateDraft,
  useTeamTemplateDrafts,
  useTeamTemplateRevision,
  useTeamTemplates,
  useValidateTeamTemplateDraft,
} from "./api";

type WorkcellDefinition = components["schemas"]["WorkcellDefinition"];
type TeamTopology = components["schemas"]["TeamTopology"];

export function TeamTemplatesPage() {
  const templates = useTeamTemplates();
  const [selectedId, setSelectedId] = useState<string>();
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!selectedId && templates.data?.[0]) setSelectedId(templates.data[0].id);
  }, [selectedId, templates.data]);

  if (templates.isLoading) return <LoadingState label="正在读取组织模板与冻结 Revision…"/>;
  if (templates.error) return <ErrorState error={templates.error} retry={() => templates.refetch()}/>;
  const selected = templates.data?.find((item) => item.id === selectedId);

  return <div className="team-template-workbench">
    <aside className="team-catalog panel">
      <div className="panel-head"><span>TeamTemplate</span><small>组织权威，不定义 Pipeline Stage</small></div>
      <div className="team-catalog-list">{templates.data?.map((item) => <Button type="text" className={item.id === selectedId ? "selected" : ""} key={item.id} onClick={() => { setSelectedId(item.id); setCreating(false); }}><Boxes size={17}/><span><b>{item.name}</b><small>{item.id} · {item.latest_revision ? `R${item.latest_revision}` : "未发布"}</small></span></Button>)}</div>
      <Button type="primary" icon={<Plus size={15}/>} onClick={() => setCreating(true)}>新建组织模板</Button>
    </aside>
    <main className="team-editor-shell">
      {creating ? <CreateTeamTemplate onCreated={(id) => { setSelectedId(id); setCreating(false); }}/> : selected ? <TeamTemplateWorkspace template={selected}/> : <EmptyState title="选择一个组织模板" detail="这里编辑 Workcell 身份、Workspace 要求和委派上限；Stage、Provider 与发布顺序仍由各自权威管理。"/>}
    </main>
  </div>;
}

function CreateTeamTemplate({ onCreated }: { onCreated: (id: string) => void }) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const create = useCreateTeamTemplate();
  return <section className="panel team-create-card">
    <span className="eyebrow">新组织草稿</span><h2>从四仓 Workcell 骨架开始</h2>
    <p>创建后可以增删 Workcell、调整职责和 Artifact 拓扑。页面不会提供 Stage 顺序、Provider、凭证或 Release Participant 字段。</p>
    <label>模板标识<Input value={id} onChange={(event) => setId(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} placeholder="例如：commerce-delivery-team"/></label>
    <label>模板名称<Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：电商四仓交付团队"/></label>
    <Button type="primary" disabled={!id || !name.trim()} loading={create.isPending} onClick={() => create.mutate(starterTeamTemplate(id, name.trim()), { onSuccess: ({ template }) => onCreated(template.id) })}>创建 Draft</Button>
    {create.error && (
      <ErrorState error={create.error}/>
    )}
  </section>;
}

function TeamTemplateWorkspace({ template }: { template: TeamTemplate }) {
  const drafts = useTeamTemplateDrafts(template.id);
  const revision = useTeamTemplateRevision(template);
  const draft = drafts.data?.[0];
  if (drafts.isLoading || revision.isLoading) return <LoadingState label="正在装载组织拓扑…"/>;
  if (drafts.error) return <ErrorState error={drafts.error} retry={() => drafts.refetch()}/>;
  if (!draft) return <section className="panel immutable-team"><span className="eyebrow">Published Revision</span><h2>{template.name} · R{template.latest_revision}</h2><p>该内置 Revision 是不可变组织权威。若要调整组织，请创建新的 TeamTemplate；不要把 Pipeline Stage 顺序复制进 Team。</p>{revision.data && <TopologyCanvas workcells={revision.data.workcells} topology={revision.data.topology}/>}<code>{revision.data?.sha256}</code></section>;
  return <DraftEditor key={`${draft.id}:${draft.version}`} draft={draft}/>;
}

function DraftEditor({ draft }: { draft: TeamTemplateDraft }) {
  const [name, setName] = useState(draft.name);
  const [description, setDescription] = useState(draft.description);
  const [workcells, setWorkcells] = useState(draft.workcells);
  const [topology, setTopology] = useState(draft.topology);
  const patch = usePatchTeamTemplateDraft(draft.template_id);
  const validate = useValidateTeamTemplateDraft(draft.template_id);
  const publish = usePublishTeamTemplateDraft(draft.template_id);
  const editorError = patch.error ?? validate.error ?? publish.error;
  const workcellKeys = useMemo(() => workcells.map((item) => item.workcell_key), [workcells]);
  const busy = patch.isPending || validate.isPending || publish.isPending;
  const updateWorkcell = (key: string, update: Partial<WorkcellDefinition>) => setWorkcells((current) => current.map((item) => item.workcell_key === key ? { ...item, ...update } : item));
  const removeWorkcell = (key: string) => {
    setWorkcells((current) => current.filter((item) => item.workcell_key !== key));
    setTopology((current) => ({ nodes: current.nodes.filter((item) => item.workcell_key !== key), links: current.links.filter((item) => item.source_workcell_key !== key && item.target_workcell_key !== key) }));
  };
  const addWorkcell = () => {
    const key = uniqueWorkcellKey(workcellKeys);
    setWorkcells((current) => [...current, starterWorkcell(key, `Workcell ${current.length + 1}`, "定义该 Workcell 的交付职责。")]);
    setTopology((current) => ({ ...current, nodes: [...current.nodes, { workcell_key: key, x: 100 + current.nodes.length * 220, y: 180 }] }));
  };
  const updateTargets = (source: string, targets: string[]) => setTopology((current) => ({ ...current, links: [...current.links.filter((item) => item.source_workcell_key !== source), ...targets.map((target) => ({ source_workcell_key: source, target_workcell_key: target, label: "artifact" }))] }));

  return <div className="team-draft-editor">
    <section className="panel team-editor-command"><div><span className="eyebrow">Draft · v{draft.version}</span><h2>{name}</h2><p>只编辑组织拓扑和 Workcell 资源边界。执行顺序由 Published Pipeline Revision 管理。</p></div><div className="team-editor-actions"><StatusBadge value={draft.validation_status}/><Button icon={<Save size={15}/>} disabled={busy || !workcells.length} onClick={() => patch.mutate({ draftId: draft.id, patch: { expected_version: draft.version, name, description, workcells, topology } })}>保存变更</Button><Button icon={<ShieldCheck size={15}/>} disabled={busy} onClick={() => validate.mutate({ draftId: draft.id, expectedVersion: draft.version })}>校验组织</Button><Button type="primary" icon={<Send size={15}/>} disabled={busy || draft.validation_status !== "valid"} onClick={() => publish.mutate({ draftId: draft.id, expectedVersion: draft.version })}>发布 Revision</Button></div></section>
    {editorError && (
      <ErrorState error={editorError}/>
    )}
    {draft.validation_errors.length > 0 && <section className="team-validation-errors" role="alert"><b>组织校验未通过</b>{draft.validation_errors.map((item) => <code key={item}>{item}</code>)}</section>}
    <section className="panel team-metadata"><label>名称<Input value={name} onChange={(event) => setName(event.target.value)}/></label><label>职责说明<Input.TextArea rows={2} value={description} onChange={(event) => setDescription(event.target.value)}/></label></section>
    <section className="team-topology-layout">
      <div className="panel topology-panel"><div className="panel-head"><span>Artifact 拓扑</span><small>连线表达只读 ArtifactAttachment，不表达执行顺序</small></div><TopologyCanvas workcells={workcells} topology={topology}/></div>
      <div className="workcell-definition-list"><div className="workcell-list-heading"><div><b>Workcell 定义</b><small>{workcells.length} 个独立 Primary Repository 要求</small></div><Button icon={<Plus size={14}/>} onClick={addWorkcell}>添加 Workcell</Button></div>{workcells.map((workcell) => <WorkcellDefinitionCard key={workcell.workcell_key} workcell={workcell} workcellKeys={workcellKeys} topology={topology} onlyWorkcell={workcells.length === 1} update={(value) => updateWorkcell(workcell.workcell_key, value)} remove={() => removeWorkcell(workcell.workcell_key)} updateTargets={(targets) => updateTargets(workcell.workcell_key, targets)}/>)}</div>
    </section>
  </div>;
}

function WorkcellDefinitionCard({ workcell, workcellKeys, topology, onlyWorkcell, update, remove, updateTargets }: { workcell: WorkcellDefinition; workcellKeys: string[]; topology: TeamTopology; onlyWorkcell: boolean; update: (value: Partial<WorkcellDefinition>) => void; remove: () => void; updateTargets: (targets: string[]) => void }) {
  const policy = workcell.delegation_policy ?? { max_children: 3, max_concurrency: 2, max_writers: 1, max_depth: 1 as const, wall_clock_budget_seconds: 900 };
  return <article className="workcell-definition"><header><span><GitFork size={15}/><code>{workcell.workcell_key}</code></span><Button type="text" danger aria-label={`删除 ${workcell.name}`} icon={<Trash2 size={14}/>} disabled={onlyWorkcell} onClick={remove}/></header><label>显示名称<Input value={workcell.name} onChange={(event) => update({ name: event.target.value })}/></label><label>职责<Input.TextArea rows={2} value={workcell.responsibility} onChange={(event) => update({ responsibility: event.target.value })}/></label><div className="workcell-policy-grid"><label>Child 上限<InputNumber min={0} max={3} value={policy.max_children} onChange={(value) => update({ delegation_policy: { ...policy, max_children: Number(value ?? 0) } })}/></label><label>并发上限<InputNumber min={1} max={2} value={policy.max_concurrency} onChange={(value) => update({ delegation_policy: { ...policy, max_concurrency: Number(value ?? 1) } })}/></label><label>Writer 上限<InputNumber min={0} max={1} value={policy.max_writers} onChange={(value) => update({ delegation_policy: { ...policy, max_writers: Number(value ?? 0) } })}/></label><label>预算（秒）<InputNumber min={30} max={3600} value={policy.wall_clock_budget_seconds} onChange={(value) => update({ delegation_policy: { ...policy, wall_clock_budget_seconds: Number(value ?? 900) } })}/></label></div><label>允许的 Delegate Purpose<Select mode="multiple" value={workcell.delegate_purposes} onChange={(value) => update({ delegate_purposes: value as WorkcellDefinition["delegate_purposes"] })} options={["workspace_write", "artifact", "review"].map((value) => ({ value, label: value }))}/></label><label>Artifact 输出到<Select mode="multiple" value={topology.links.filter((item) => item.source_workcell_key === workcell.workcell_key).map((item) => item.target_workcell_key)} onChange={updateTargets} options={workcellKeys.filter((key) => key !== workcell.workcell_key).map((value) => ({ value, label: value }))}/></label></article>;
}

function TopologyCanvas({ workcells, topology }: { workcells: WorkcellDefinition[]; topology: TeamTopology }) {
  const nodes = topology.nodes.map((node, index) => ({ ...node, x: Math.max(4, Math.min(84, node.x / 9)), y: Math.max(8, Math.min(76, node.y / 5 + index % 2 * 3)) }));
  const byKey = new Map(nodes.map((node) => [node.workcell_key, node]));
  const names = new Map(workcells.map((item) => [item.workcell_key, item.name]));
  return <div className="team-topology-canvas" aria-label="Workcell Artifact 拓扑">
    <svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none">{topology.links.map((link) => { const source = byKey.get(link.source_workcell_key); const target = byKey.get(link.target_workcell_key); return source && target ? <line key={`${link.source_workcell_key}:${link.target_workcell_key}`} x1={source.x + 8} y1={source.y + 6} x2={target.x} y2={target.y + 6}/> : null; })}</svg>
    {nodes.map((node) => <div className="topology-workcell" key={node.workcell_key} style={{ left: `${node.x}%`, top: `${node.y}%` }}><span>{names.get(node.workcell_key) ?? node.workcell_key}</span><code>{node.workcell_key}</code><small>git_repository_v1</small></div>)}
  </div>;
}

export function starterTeamTemplate(id: string, name: string): TeamTemplateCreate {
  const workcells = [
    starterWorkcell("design", "Design", "设计契约与可验证设计工件"),
    starterWorkcell("frontend", "Frontend", "前端实现与 UX 边界验证"),
    starterWorkcell("backend", "Backend", "后端实现与安全边界验证"),
    starterWorkcell("qa", "QA", "测试设计、自动化、审查与追踪", ["workspace_write", "artifact", "review"]),
  ];
  return { id, name, description: "四个隔离 Repository Workcell；组织拓扑只传递 Artifact。", workcells, topology: { nodes: [{ workcell_key: "design", x: 40, y: 160 }, { workcell_key: "frontend", x: 360, y: 60 }, { workcell_key: "backend", x: 360, y: 260 }, { workcell_key: "qa", x: 700, y: 160 }], links: [{ source_workcell_key: "design", target_workcell_key: "frontend", label: "artifact" }, { source_workcell_key: "design", target_workcell_key: "backend", label: "artifact" }, { source_workcell_key: "frontend", target_workcell_key: "qa", label: "artifact" }, { source_workcell_key: "backend", target_workcell_key: "qa", label: "artifact" }] } };
}

function starterWorkcell(key: string, name: string, responsibility: string, purposes: Array<"workspace_write" | "artifact" | "review"> = ["workspace_write", "review"]): WorkcellDefinition {
  return { workcell_key: key, name, responsibility, primary_workspace: { kind: "git_repository_v1" }, delegate_purposes: purposes, delegation_policy: { max_children: 3, max_concurrency: 2, max_writers: 1, max_depth: 1, wall_clock_budget_seconds: 900 } };
}

function uniqueWorkcellKey(keys: string[]) {
  for (let index = 1; index < 100; index += 1) {
    const candidate = `workcell-${index}`;
    if (!keys.includes(candidate)) return candidate;
  }
  return `workcell-${Date.now()}`;
}
