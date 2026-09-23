import { BookLock, CircleOff, FileCheck2, Network } from "lucide-react";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useDeliveryKnowledgeContext } from "../knowledge/tenantApi";

// 仅显式读取安全投影字段；不渲染请求文本或 Provider 原文。
interface SafeQueryErrorProjection {
  code: string;
  category: string;
  http_status: number | null;
  unit: number;
  measured_tokens: number;
  allowed_tokens: number;
  measured_bytes: number;
  allowed_bytes: number;
  correlation_id: string;
}

export function KnowledgeContextPanel({ projectId, deliveryId }: { projectId: string; deliveryId: string }) {
  const overview = useDeliveryKnowledgeContext(projectId, deliveryId);

  if (overview.isLoading) return <section className="knowledge-context-panel stage-shell"><LoadingState label="正在读取 Delivery Knowledge Context…"/></section>;
  if (overview.error) return <section className="knowledge-context-panel stage-shell"><ErrorState error={overview.error} retry={() => overview.refetch()}/></section>;
  if (!overview.data) return null;

  const { preparation_run: preparation, contexts, unavailable, citations } = overview.data;
  const inputFailure = preparation?.error_code?.startsWith("KNOWLEDGE_QUERY_")
    || preparation?.error_code === "KNOWLEDGE_OLLAMA_REQUEST_FAILED";
  const queryErrors = (overview.data as { query_errors?: SafeQueryErrorProjection[] }).query_errors ?? [];
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

    {inputFailure ? <aside className="knowledge-preparation-receipt" aria-label="知识输入失败处理建议">
      <CircleOff size={17}/><div>
        <b>输入资格与预算检查未通过，或模型请求失败</b>
        <p>请核查固定模型的输入资格、精确计数与单元及总预算；缺少资格证据时保持阻塞。此错误码本身不能证明输入超限。</p>
        <p>不会截断原始需求，也不会自动重试输入限制错误。终态不会在此恢复；新交付需另行授权。</p>
      </div>
    </aside> : null}

    {queryErrors.length > 0 ? <section className="knowledge-context-records" aria-label="查询安全诊断">
      <h3>查询安全诊断（只读）</h3>
      {queryErrors.map((error) => <div key={error.correlation_id}>
        <code>{error.code}</code>
        <small>HTTP {error.http_status ?? "未知"} · {error.category} · Unit {error.unit}</small>
        <small>{error.measured_tokens} / {error.allowed_tokens} tokens</small>
        <small>{error.measured_bytes} / {error.allowed_bytes} bytes</small>
        <small>本地关联 ID <code>{error.correlation_id}</code></small>
      </div>)}
    </section> : null}

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
        </div>) : <EmptyState title="没有不可用回执" detail="没有不可用回执不代表输入已成功冻结；请结合 Preparation 状态与冻结记录判断。"/>}</div>
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
