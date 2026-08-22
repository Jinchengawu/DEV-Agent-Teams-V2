import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, ReactFlow, useNodesState, type Edge, type Node } from "@xyflow/react";
import { Activity, Bot, Boxes, Database, FileCheck2, GitBranch, LayoutDashboard, Settings, Workflow } from "lucide-react";
import { api, type Delivery, type Draft, type Instance, type Knowledge, type Revision, type WorkItem } from "./api";
import { parseJourneyRevisions } from "./contracts";
import { artifactTypeLabel, commandLabel, documentTitle, identityLabel, journeyStepLabel, runtimeTypeLabel, statusLabel } from "./i18n";

const sections = [
  ["deliveries", "交付", GitBranch], ["board", "看板", LayoutDashboard],
  ["orchestration", "可视化编排", Workflow], ["agents", "智能体实例", Bot],
  ["knowledge", "知识中心", Database], ["evidence", "证据", FileCheck2],
  ["settings", "设置", Settings],
] as const;
const columns = [
  ["backlog", "待规划"], ["plan-approval", "计划审批"], ["executing", "执行中"],
  ["candidate-approval", "候选审批"], ["completed", "已完成"], ["failed-cancelled", "失败 / 取消"],
] as const;

export function App() {
  const [section, setSection] = useState("deliveries");
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: () => api<Delivery[]>("/v1/deliveries") });
  const active = deliveries.data?.find((item) => !["completed", "failed", "rejected", "cancelled"].includes(item.status)) ?? deliveries.data?.[0];
  return <div className="app-shell">
    <aside>
      <div className="brand"><span className="brand-mark"><Boxes size={20}/></span><div><b>Agent-Team-OS</b><small>控制平面 · V0.2</small></div></div>
      <nav>{sections.map(([id, label, Icon]) => <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}><Icon size={17}/>{label}</button>)}</nav>
      <div className="system-state"><span className="pulse"/>系统在线<small>本地运行 / 证据模式</small></div>
    </aside>
    <main>
      <header><div><p className="kicker">团队协作控制层</p><h1>{sections.find(([id]) => id === section)?.[1]}</h1></div><div className="identity-chip"><Activity size={16}/><span>规划身份<br/><b>Codex 模拟 Hermes</b></span><span>执行身份<br/><b>Codex 命令行</b></span></div></header>
      <OperatingMap delivery={active}/>
      {section === "deliveries" && <Deliveries deliveries={deliveries.data ?? []}/>}
      {section === "board" && <Board/>}
      {section === "orchestration" && <Orchestration/>}
      {section === "agents" && <Agents/>}
      {section === "knowledge" && <KnowledgeCenter/>}
      {section === "evidence" && <Evidence delivery={active}/>}
      {section === "settings" && <SettingsView/>}
    </main>
  </div>;
}

function OperatingMap({ delivery }: { delivery?: Delivery }) {
  const states = ["需求", "计划审批", "执行", "验证", "候选审批", "应用"];
  const index: Record<string, number> = { queued:0, planning:0, awaiting_plan_decision:1, executing:2, verifying:3, awaiting_candidate_decision:4, applying:5, completed:6 };
  const current = delivery ? (index[delivery.status] ?? -1) : -1;
  return <section className="operating-map"><div className="map-label"><span>运行态势图</span><b>{delivery ? statusLabel(delivery.status) : "空闲"}</b></div><div className="map-track">{states.map((state, i) => <div key={state} className={i < current ? "done" : i === current ? "live" : ""}><i>{String(i + 1).padStart(2,"0")}</i><strong>{state}</strong><small>{i === 1 || i === 4 ? "人工授权" : "机器控制"}</small></div>)}</div></section>;
}

function Deliveries({ deliveries }: { deliveries: Delivery[] }) {
  const query = useQueryClient(); const [request, setRequest] = useState("增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。");
  const create = useMutation({ mutationFn: () => api<Delivery>("/v1/deliveries", { method:"POST", body:JSON.stringify({workspace_id:"backend-demo", user_request:request}) }), onSuccess: () => query.invalidateQueries({queryKey:["deliveries"]}) });
  return <div className="content-grid"><section className="panel command-panel"><div className="panel-head"><span>新建交付</span><small>内置后端沙箱</small></div><h2>把意图变成可审查的变更。</h2><textarea aria-label="交付需求" value={request} onChange={(event) => setRequest(event.target.value)}/><button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>启动交付闭环 →</button>{create.error && <p className="error">创建失败：{create.error.message}</p>}</section><section className="panel"><div className="panel-head"><span>交付历史</span><small>共 {deliveries.length} 次运行</small></div><div className="rows">{deliveries.map((item) => <article key={item.id}><div><b>{item.user_request}</b><small>{item.id.slice(0,8)} · 版本 {item.version} · {item.journey_revision_id ?? "旧版本交付"}</small></div><Status value={item.status}/></article>)}</div></section></div>;
}

function Board() {
  const client = useQueryClient(); const board = useQuery({queryKey:["board"], queryFn:()=>api<WorkItem[]>("/v1/board")});
  const command = useMutation({mutationFn:({item, command}:{item:WorkItem;command:string})=>api(`/v1/work-items/${item.id}/command`,{method:"POST",body:JSON.stringify({command,expected_version:item.version})}),onSuccess:()=>{client.invalidateQueries({queryKey:["board"]});client.invalidateQueries({queryKey:["deliveries"]});}});
  return <section className="board">{columns.map(([id,label])=><div className="board-column" key={id}><div className="column-head"><b>{label}</b><span>{board.data?.filter(i=>i.column===id).length ?? 0}</span></div>{board.data?.filter(i=>i.column===id).map(item=><article className="work-card" key={item.id}><small>交付 {item.id.slice(0,8)}</small><h3>{item.title}</h3><p>{item.acceptance_ids.join(" · ") || "等待任务合同"}</p><div>{item.available_commands.map(action=><button key={action} onClick={()=>command.mutate({item,command:action})}>{commandLabel(action)}</button>)}</div></article>)}</div>)}</section>;
}

function Orchestration() {
  const client=useQueryClient(); const journeys = useQuery({queryKey:["journeys"],queryFn:async()=>parseJourneyRevisions(await api<unknown>("/v1/journeys"))}); const drafts=useQuery({queryKey:["drafts"],queryFn:()=>api<Draft[]>("/v1/journey-drafts")}); const revision=journeys.data?.[0]; const draft=drafts.data?.at(-1); const definition=draft?.definition??revision?.definition;
  const initialNodes = useMemo<Node[]>(()=>definition?.steps?.map((step,index)=>({id:String(step.id),position:{x:index*220,y:index%2?110:30},data:{label:<><small>{step.kind==="approval_gate"?"审批关卡":"执行阶段"}</small><b>{journeyStepLabel(String(step.id))}</b></>},className:`flow-node ${step.kind==="approval_gate"?"gate":"stage"}`}))??[],[definition]);
  const [nodes,setNodes,onNodesChange]=useNodesState(initialNodes); useEffect(()=>setNodes(initialNodes),[initialNodes,setNodes]);
  const edges=useMemo<Edge[]>(()=>nodes.slice(1).map((node,index)=>({id:`e${index}`,source:nodes[index].id,target:node.id,animated:true})),[nodes]);
  const clone=useMutation({mutationFn:()=>api<Draft>("/v1/journey-drafts",{method:"POST",body:JSON.stringify({name:"后端交付旅程草稿",definition:revision?.definition,layout:{}})}),onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})});
  const save=useMutation({mutationFn:()=>{const ordered=[...nodes].sort((a,b)=>a.position.x-b.position.x);const byId=new Map(definition?.steps?.map(step=>[String(step.id),step]));return api<Draft>(`/v1/journey-drafts/${draft?.id}`,{method:"PATCH",body:JSON.stringify({expected_version:draft?.version,definition:{...definition,steps:ordered.map(node=>byId.get(node.id))},layout:Object.fromEntries(nodes.map(n=>[n.id,n.position]))})})},onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})});
  const validate=useMutation({mutationFn:()=>api<Draft>(`/v1/journey-drafts/${draft?.id}/validate`,{method:"POST"}),onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})}); const publish=useMutation({mutationFn:()=>api<Revision>(`/v1/journey-drafts/${draft?.id}/publish`,{method:"POST"}),onSuccess:()=>{client.invalidateQueries({queryKey:["journeys"]});client.invalidateQueries({queryKey:["drafts"]});}});
  return <section className="panel orchestration"><div className="panel-head"><span>线性交付旅程</span><small>{draft?`草稿 · 版本 ${draft.version} · ${statusLabel(draft.validation_status)}`:revision?`后端交付 · 发布版本 ${revision.revision}`:"尚无发布版本"}</small></div><div className="orchestration-actions">{!draft&&revision&&<button onClick={()=>clone.mutate()}>克隆为草稿</button>}{draft&&<><button onClick={()=>save.mutate()}>保存节点顺序</button><button onClick={()=>validate.mutate()}>ACWM 校验</button><button className="primary" onClick={()=>publish.mutate()}>发布不可变版本</button></>}</div><div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={Boolean(draft)} nodesConnectable={false} onNodesChange={onNodesChange}><Background gap={24}/></ReactFlow></div>{draft?.validation_errors.length?<p className="error">校验失败：{draft.validation_errors.join(" · ")}</p>:null}<div className="revision-strip"><span>不可变 SHA-256</span><code>{revision?.fingerprint ?? "—"}</code></div></section>;
}

function Agents() {
  const client=useQueryClient(); const instances=useQuery({queryKey:["agents"],queryFn:()=>api<Instance[]>("/v1/agent-instances")}); const [name,setName]=useState("");
  const create=useMutation({mutationFn:()=>api<Instance>("/v1/agent-instances",{method:"POST",body:JSON.stringify({name,runtime_type:"codex-cli",connection:{command:"codex"},features:["cwd-binding","workspace-write"]})}),onSuccess:()=>{setName("");client.invalidateQueries({queryKey:["agents"]});}});
  const health=useMutation({mutationFn:(id:string)=>api(`/v1/agent-instances/${id}/health-check`,{method:"POST"}),onSuccess:()=>client.invalidateQueries({queryKey:["agents"]})});
  return <div className="content-grid"><section className="panel"><div className="panel-head"><span>智能体实例注册表</span><small>仅保存凭据引用</small></div><div className="rows">{instances.data?.map(item=><article key={item.id}><div><b>{item.name}</b><small>{runtimeTypeLabel(item.runtime_type)} · 版本 {item.version} · {identityLabel(item.health.identity)}</small></div><div className="row-actions"><Status value={item.health.status}/><button onClick={()=>health.mutate(item.id)}>执行健康检查</button></div></article>)}</div></section><section className="panel compact-form"><div className="panel-head"><span>注册 Codex 实例</span><small>本地实例</small></div><label>实例名称<input value={name} onChange={e=>setName(e.target.value)} placeholder="Codex 后端实例 01"/></label><button className="primary" disabled={!name} onClick={()=>create.mutate()}>注册实例</button></section></div>;
}

function KnowledgeCenter() {
  const [q,setQ]=useState(""); const docs=useQuery({queryKey:["knowledge",q],queryFn:()=>api<Knowledge[]>(`/v1/knowledge/search?q=${encodeURIComponent(q)}`),enabled:q.length>0}); const [selected,setSelected]=useState<Knowledge>();
  return <div className="content-grid knowledge"><section className="panel"><div className="panel-head"><span>可追溯知识</span><small>全文检索 / 不使用检索增强生成</small></div><input className="search" value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索交付、验收编号或产物…"/><div className="rows">{docs.data?.map(doc=><article key={doc.id} onClick={()=>setSelected(doc)}><div><b>{documentTitle(doc.title, doc.artifact_type)}</b><small>{artifactTypeLabel(doc.artifact_type)} · 版本 {doc.revision}</small></div><code>{doc.sha256.slice(0,12)}</code></article>)}</div></section><section className="panel document"><div className="panel-head"><span>来源检查器</span><small>{selected?artifactTypeLabel(selected.artifact_type):"请选择文档"}</small></div>{selected?<><h2>{documentTitle(selected.title, selected.artifact_type)}</h2><p className="provenance">{selected.sources.map(s=>`${artifactTypeLabel(s.source_kind)} / ${s.source_id}`).join(" · ")}</p><pre>{selected.content}</pre></>:<div className="empty-state">每条知识都必须能回到原始产物与不可变哈希。</div>}</section></div>;
}

function Evidence({delivery}:{delivery?:Delivery}) { return <section className="panel evidence"><div className="panel-head"><span>证据账本</span><small>{delivery?.id??"尚无交付"}</small></div>{delivery?<div className="evidence-grid"><EvidenceBlock label="交付旅程哈希" value={delivery.resolved_journey_sha256}/><EvidenceBlock label="计划审批主题哈希" value={delivery.plan_gate?.subject_sha256}/><EvidenceBlock label="候选版本" value={delivery.candidate?.candidate_revision}/><EvidenceBlock label="差异哈希" value={delivery.candidate?.diff_sha256}/><EvidenceBlock label="测试日志哈希" value={delivery.verification?.log_sha256}/><EvidenceBlock label="应用回执" value={delivery.apply_receipt?JSON.stringify(delivery.apply_receipt,null,2):undefined}/></div>:<div className="empty-state">尚无可检查证据。</div>}</section>; }
function EvidenceBlock({label,value}:{label:string;value?:string}) { return <div><small>{label}</small><pre>{value??"—"}</pre></div>; }
function SettingsView(){return <section className="panel"><div className="panel-head"><span>运行策略</span><small>V0.2 锁定范围</small></div><div className="policy-list"><p><b>执行方式</b> Codex 命令行 · <code>workspace-write</code> · 隔离 Git 工作树</p><p><b>允许路径</b> <code>src/**</code> · <code>tests/**</code></p><p><b>固定验证</b> <code>python -m unittest discover -s tests -v</code></p><p><b>知识存储</b> SQLite 全文检索 · 必须保留来源 · 不使用向量嵌入</p></div></section>}
function Status({value}:{value:string}){return <span className={`status status-${value.replaceAll("_","-")}`}>{statusLabel(value)}</span>}
