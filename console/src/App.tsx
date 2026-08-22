import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, ReactFlow, useNodesState, type Edge, type Node } from "@xyflow/react";
import { Activity, Bot, Boxes, Database, FileCheck2, GitBranch, LayoutDashboard, Settings, Workflow } from "lucide-react";
import { api, type Delivery, type Draft, type Instance, type Knowledge, type Revision, type WorkItem } from "./api";
import { parseJourneyRevisions } from "./contracts";

const sections = [
  ["Deliveries", GitBranch], ["Board", LayoutDashboard], ["Orchestration", Workflow],
  ["Agents", Bot], ["Knowledge", Database], ["Evidence", FileCheck2], ["Settings", Settings],
] as const;
const columns = [
  ["backlog", "待规划"], ["plan-approval", "计划审批"], ["executing", "执行中"],
  ["candidate-approval", "候选审批"], ["completed", "已完成"], ["failed-cancelled", "失败 / 取消"],
] as const;

export function App() {
  const [section, setSection] = useState("Deliveries");
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: () => api<Delivery[]>("/v1/deliveries") });
  const active = deliveries.data?.find((item) => !["completed", "failed", "rejected", "cancelled"].includes(item.status)) ?? deliveries.data?.[0];
  return <div className="app-shell">
    <aside>
      <div className="brand"><span className="brand-mark"><Boxes size={20}/></span><div><b>AGENT-TEAM-OS</b><small>CONTROL PLANE · V0.2</small></div></div>
      <nav>{sections.map(([name, Icon]) => <button key={name} className={section === name ? "active" : ""} onClick={() => setSection(name)}><Icon size={17}/>{name}</button>)}</nav>
      <div className="system-state"><span className="pulse"/>SYSTEM ONLINE<small>LOCAL / EVIDENCE MODE</small></div>
    </aside>
    <main>
      <header><div><p className="kicker">TEAM COORDINATION LAYER</p><h1>{section}</h1></div><div className="identity-chip"><Activity size={16}/><span>PLANNING<br/><b>codex-simulated-hermes</b></span><span>EXECUTION<br/><b>codex-cli</b></span></div></header>
      <OperatingMap delivery={active}/>
      {section === "Deliveries" && <Deliveries deliveries={deliveries.data ?? []}/>} 
      {section === "Board" && <Board/>}
      {section === "Orchestration" && <Orchestration/>}
      {section === "Agents" && <Agents/>}
      {section === "Knowledge" && <KnowledgeCenter/>}
      {section === "Evidence" && <Evidence delivery={active}/>} 
      {section === "Settings" && <SettingsView/>}
    </main>
  </div>;
}

function OperatingMap({ delivery }: { delivery?: Delivery }) {
  const states = ["REQUEST", "PLAN GATE", "EXECUTION", "VERIFY", "CANDIDATE GATE", "APPLY"];
  const index: Record<string, number> = { queued:0, planning:0, awaiting_plan_decision:1, executing:2, verifying:3, awaiting_candidate_decision:4, applying:5, completed:6 };
  const current = delivery ? (index[delivery.status] ?? -1) : -1;
  return <section className="operating-map"><div className="map-label"><span>OPERATING MAP</span><b>{delivery?.status ?? "IDLE"}</b></div><div className="map-track">{states.map((state, i) => <div key={state} className={i < current ? "done" : i === current ? "live" : ""}><i>{String(i + 1).padStart(2,"0")}</i><strong>{state}</strong><small>{i === 1 || i === 4 ? "HUMAN AUTHORITY" : "MACHINE CONTROL"}</small></div>)}</div></section>;
}

function Deliveries({ deliveries }: { deliveries: Delivery[] }) {
  const query = useQueryClient(); const [request, setRequest] = useState("增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。");
  const create = useMutation({ mutationFn: () => api<Delivery>("/v1/deliveries", { method:"POST", body:JSON.stringify({workspace_id:"backend-demo", user_request:request}) }), onSuccess: () => query.invalidateQueries({queryKey:["deliveries"]}) });
  return <div className="content-grid"><section className="panel command-panel"><div className="panel-head"><span>NEW DELIVERY</span><small>BACKEND-DEMO</small></div><h2>把意图变成可审查的变更。</h2><textarea value={request} onChange={(event) => setRequest(event.target.value)}/><button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>启动交付闭环 →</button>{create.error && <p className="error">{create.error.message}</p>}</section><section className="panel"><div className="panel-head"><span>DELIVERY HISTORY</span><small>{deliveries.length} RUNS</small></div><div className="rows">{deliveries.map((item) => <article key={item.id}><div><b>{item.user_request}</b><small>{item.id.slice(0,8)} · V{item.version} · {item.journey_revision_id ?? "LEGACY"}</small></div><Status value={item.status}/></article>)}</div></section></div>;
}

function Board() {
  const client = useQueryClient(); const board = useQuery({queryKey:["board"], queryFn:()=>api<WorkItem[]>("/v1/board")});
  const command = useMutation({mutationFn:({item, command}:{item:WorkItem;command:string})=>api(`/v1/work-items/${item.id}/command`,{method:"POST",body:JSON.stringify({command,expected_version:item.version})}),onSuccess:()=>{client.invalidateQueries({queryKey:["board"]});client.invalidateQueries({queryKey:["deliveries"]});}});
  return <section className="board">{columns.map(([id,label])=><div className="board-column" key={id}><div className="column-head"><b>{label}</b><span>{board.data?.filter(i=>i.column===id).length ?? 0}</span></div>{board.data?.filter(i=>i.column===id).map(item=><article className="work-card" key={item.id}><small>{item.id.slice(0,8)}</small><h3>{item.title}</h3><p>{item.acceptance_ids.join(" · ") || "等待任务合同"}</p><div>{item.available_commands.map(action=><button key={action} onClick={()=>command.mutate({item,command:action})}>{action}</button>)}</div></article>)}</div>)}</section>;
}

function Orchestration() {
  const client=useQueryClient(); const journeys = useQuery({queryKey:["journeys"],queryFn:async()=>parseJourneyRevisions(await api<unknown>("/v1/journeys"))}); const drafts=useQuery({queryKey:["drafts"],queryFn:()=>api<Draft[]>("/v1/journey-drafts")}); const revision=journeys.data?.[0]; const draft=drafts.data?.at(-1); const definition=draft?.definition??revision?.definition;
  const initialNodes = useMemo<Node[]>(()=>definition?.steps?.map((step,index)=>({id:String(step.id),position:{x:index*220,y:index%2?110:30},data:{label:<><small>{String(step.kind).toUpperCase()}</small><b>{String(step.id)}</b></>},className:`flow-node ${step.kind==="approval_gate"?"gate":"stage"}`}))??[],[definition]);
  const [nodes,setNodes,onNodesChange]=useNodesState(initialNodes); useEffect(()=>setNodes(initialNodes),[initialNodes,setNodes]);
  const edges=useMemo<Edge[]>(()=>nodes.slice(1).map((node,index)=>({id:`e${index}`,source:nodes[index].id,target:node.id,animated:true})),[nodes]);
  const clone=useMutation({mutationFn:()=>api<Draft>("/v1/journey-drafts",{method:"POST",body:JSON.stringify({name:"Backend delivery draft",definition:revision?.definition,layout:{}})}),onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})});
  const save=useMutation({mutationFn:()=>{const ordered=[...nodes].sort((a,b)=>a.position.x-b.position.x);const byId=new Map(definition?.steps?.map(step=>[String(step.id),step]));return api<Draft>(`/v1/journey-drafts/${draft?.id}`,{method:"PATCH",body:JSON.stringify({expected_version:draft?.version,definition:{...definition,steps:ordered.map(node=>byId.get(node.id))},layout:Object.fromEntries(nodes.map(n=>[n.id,n.position]))})})},onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})});
  const validate=useMutation({mutationFn:()=>api<Draft>(`/v1/journey-drafts/${draft?.id}/validate`,{method:"POST"}),onSuccess:()=>client.invalidateQueries({queryKey:["drafts"]})}); const publish=useMutation({mutationFn:()=>api<Revision>(`/v1/journey-drafts/${draft?.id}/publish`,{method:"POST"}),onSuccess:()=>{client.invalidateQueries({queryKey:["journeys"]});client.invalidateQueries({queryKey:["drafts"]});}});
  return <section className="panel orchestration"><div className="panel-head"><span>LINEAR JOURNEY</span><small>{draft?`DRAFT · V${draft.version} · ${draft.validation_status}`:revision?`${revision.journey_id} · REV ${revision.revision}`:"NO REVISION"}</small></div><div className="orchestration-actions">{!draft&&revision&&<button onClick={()=>clone.mutate()}>克隆为 Draft</button>}{draft&&<><button onClick={()=>save.mutate()}>保存节点顺序</button><button onClick={()=>validate.mutate()}>ACWM 校验</button><button className="primary" onClick={()=>publish.mutate()}>发布不可变 Revision</button></>}</div><div className="flow"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={Boolean(draft)} nodesConnectable={false} onNodesChange={onNodesChange}><Background gap={24}/><Controls/></ReactFlow></div>{draft?.validation_errors.length?<p className="error">{draft.validation_errors.join(" · ")}</p>:null}<div className="revision-strip"><span>IMMUTABLE SHA-256</span><code>{revision?.fingerprint ?? "—"}</code></div></section>;
}

function Agents() {
  const client=useQueryClient(); const instances=useQuery({queryKey:["agents"],queryFn:()=>api<Instance[]>("/v1/agent-instances")}); const [name,setName]=useState("");
  const create=useMutation({mutationFn:()=>api<Instance>("/v1/agent-instances",{method:"POST",body:JSON.stringify({name,runtime_type:"codex-cli",connection:{command:"codex"},features:["cwd-binding","workspace-write"]})}),onSuccess:()=>{setName("");client.invalidateQueries({queryKey:["agents"]});}});
  const health=useMutation({mutationFn:(id:string)=>api(`/v1/agent-instances/${id}/health-check`,{method:"POST"}),onSuccess:()=>client.invalidateQueries({queryKey:["agents"]})});
  return <div className="content-grid"><section className="panel"><div className="panel-head"><span>INSTANCE REGISTRY</span><small>SECRET-REFERENCE ONLY</small></div><div className="rows">{instances.data?.map(item=><article key={item.id}><div><b>{item.name}</b><small>{item.runtime_type} · V{item.version} · {item.health.identity??"NO IDENTITY"}</small></div><div className="row-actions"><Status value={item.health.status}/><button onClick={()=>health.mutate(item.id)}>探测</button></div></article>)}</div></section><section className="panel compact-form"><div className="panel-head"><span>REGISTER CODEX</span><small>LOCAL INSTANCE</small></div><label>实例名称<input value={name} onChange={e=>setName(e.target.value)} placeholder="Codex Backend 01"/></label><button className="primary" disabled={!name} onClick={()=>create.mutate()}>注册实例</button></section></div>;
}

function KnowledgeCenter() {
  const [q,setQ]=useState("health"); const docs=useQuery({queryKey:["knowledge",q],queryFn:()=>api<Knowledge[]>(`/v1/knowledge/search?q=${encodeURIComponent(q)}`),enabled:q.length>0}); const [selected,setSelected]=useState<Knowledge>();
  return <div className="content-grid knowledge"><section className="panel"><div className="panel-head"><span>TRACEABLE KNOWLEDGE</span><small>FTS5 / NO RAG</small></div><input className="search" value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索 Delivery、Acceptance ID、Artifact…"/><div className="rows">{docs.data?.map(doc=><article key={doc.id} onClick={()=>setSelected(doc)}><div><b>{doc.title}</b><small>{doc.artifact_type} · REV {doc.revision}</small></div><code>{doc.sha256.slice(0,12)}</code></article>)}</div></section><section className="panel document"><div className="panel-head"><span>SOURCE INSPECTOR</span><small>{selected?.artifact_type??"SELECT A DOCUMENT"}</small></div>{selected?<><h2>{selected.title}</h2><p className="provenance">{selected.sources.map(s=>`${s.source_kind} / ${s.source_id}`).join(" · ")}</p><pre>{selected.content}</pre></>:<div className="empty-state">每条知识都必须能回到原始 Artifact 与不可变哈希。</div>}</section></div>;
}

function Evidence({delivery}:{delivery?:Delivery}) { return <section className="panel evidence"><div className="panel-head"><span>EVIDENCE LEDGER</span><small>{delivery?.id??"NO DELIVERY"}</small></div>{delivery?<div className="evidence-grid"><EvidenceBlock label="JOURNEY SHA" value={delivery.resolved_journey_sha256}/><EvidenceBlock label="PLAN GATE" value={delivery.plan_gate?.subject_sha256}/><EvidenceBlock label="CANDIDATE REVISION" value={delivery.candidate?.candidate_revision}/><EvidenceBlock label="DIFF SHA" value={delivery.candidate?.diff_sha256}/><EvidenceBlock label="TEST LOG SHA" value={delivery.verification?.log_sha256}/><EvidenceBlock label="APPLY RECEIPT" value={delivery.apply_receipt?JSON.stringify(delivery.apply_receipt,null,2):undefined}/></div>:<div className="empty-state">尚无可检查证据。</div>}</section>; }
function EvidenceBlock({label,value}:{label:string;value?:string}) { return <div><small>{label}</small><pre>{value??"—"}</pre></div>; }
function SettingsView(){return <section className="panel"><div className="panel-head"><span>RUNTIME POLICY</span><small>V0.2 LOCKED SCOPE</small></div><div className="policy-list"><p><b>Execution</b> Codex CLI · workspace-write · isolated Worktree</p><p><b>Allowed paths</b> src/** · tests/**</p><p><b>Verification</b> python -m unittest discover -s tests -v</p><p><b>Knowledge</b> SQLite FTS5 · provenance required · no embeddings</p></div></section>}
function Status({value}:{value:string}){return <span className={`status status-${value.replaceAll("_","-")}`}>{value}</span>}
