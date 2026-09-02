import { BookLock, CircleOff, FileCheck2, Network } from "lucide-react";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useDeliveryKnowledgeContext } from "../knowledge/tenantApi";

export function KnowledgeContextPanel({ projectId, deliveryId }: { projectId: string; deliveryId: string }) {
  const overview = useDeliveryKnowledgeContext(projectId, deliveryId);

  if (overview.isLoading) return <section className="knowledge-context-panel stage-shell"><LoadingState label="正在读取 Delivery Knowledge Context…"/></section>;
  if (overview.error) return <section className="knowledge-context-panel stage-shell"><ErrorState error={overview.error} retry={() => overview.refetch()}/></section>;
  if (!overview.data) return null;

  const { preparation_run: preparation, contexts, unavailable, citations } = overview.data;
  return <section className="knowledge-context-panel stage-shell evidence-rail">
    <header className="knowledge-context-head">
      <div><span className="eyebrow">FROZEN DATA CONTEXT</span><h2>Delivery Knowledge Context</h2><p>外部知识按 ACWM Artifact Contract 冻结；它是 <code>external-collaborative</code> 数据，不具备指令权威。</p></div>
      <StatusBadge value={preparation?.status ?? "not_required"}/>
    </header>

    <div className="knowledge-context-summary">
      <span><b>{contexts.length}</b> Frozen Context</span>
      <span><b>{unavailable.length}</b> Unavailable Receipt</span>
      <span><b>{citations.length}</b> Used Citation</span>
      <span><b>{preparation?.attempt_count ?? 0}</b> Preparation Attempt</span>
    </div>

    {preparation && <div className="knowledge-preparation-receipt">
      <BookLock size={17}/><div><b>Preparation Run · {preparation.id}</b><small>Input SHA-256 <code>{preparation.input_sha256}</code></small><small>Knowledge Binding Hash <code>{preparation.knowledge_binding_hash}</code></small><small>Authorization Epoch <code>{preparation.authorization_epoch_hash ?? "未冻结"}</code></small>{preparation.error_code && <small>错误码 <code>{preparation.error_code}</code></small>}</div>
    </div>}

    {!preparation && contexts.length === 0 && unavailable.length === 0 ? <EmptyState title="该 Delivery 未要求外部知识上下文" detail="Legacy 或未声明 Knowledge Context Binding 的 Pipeline 不会被补造上下文。"/> : <div className="knowledge-context-grid">
      <article>
        <header><FileCheck2 size={17}/><div><h3>冻结的 Stage 输入</h3><p>Artifact SHA、Citation 与授权纪元一起进入不可变 Delivery Snapshot。</p></div></header>
        <div className="knowledge-context-records">{contexts.length ? contexts.map((context) => <div key={context.stage_path}>
          <span><b>{context.stage_path}</b><code>{context.artifact_reference.sha256}</code></span>
          <StatusBadge value="frozen"/>
          <small>{context.citation_ids.length} citation · {context.artifact_reference.size_bytes} bytes</small>
        </div>) : <EmptyState title="没有成功冻结的上下文" detail="查看 Preparation 状态与不可用回执。"/>}</div>
      </article>

      <article>
        <header><CircleOff size={17}/><div><h3>Unavailable Receipt</h3><p>可选输入失败会形成内容寻址回执；必需输入失败则阻止 Delivery 继续。</p></div></header>
        <div className="knowledge-context-records">{unavailable.length ? unavailable.map((item) => <div key={item.stage_path}>
          <span><b>{item.stage_path}</b><code>{item.receipt_reference.sha256}</code></span>
          <StatusBadge value="unavailable"/>
          <small>{item.error_code}</small>
        </div>) : <EmptyState title="没有不可用回执" detail="所有声明输入均已冻结，或 Pipeline 未声明外部知识。"/>}</div>
      </article>

      <article className="knowledge-citation-ledger">
        <header><Network size={17}/><div><h3>Citation → Workcell 投影</h3><p>Main/Child 输出只能引用冻结 Citation；这里显示真正被 WorkcellResult 接纳的用量。</p></div></header>
        <div className="knowledge-citation-records">{citations.length ? citations.map((citation) => <div key={citation.citation_id}>
          <code>{citation.citation_id}</code>
          <span>{citation.stage_paths.map((stagePath) => <small key={stagePath}>{stagePath}</small>)}</span>
          <span>{citation.workcell_run_ids.length ? citation.workcell_run_ids.map((runId) => <small key={runId}>{runId}</small>) : <small>尚未被 WorkcellResult 使用</small>}</span>
        </div>) : <EmptyState title="尚无运行期 Citation 使用记录" detail="上下文已冻结不代表 Agent 已实际引用；运行后才会产生用量投影。"/>}</div>
      </article>
    </div>}
  </section>;
}
