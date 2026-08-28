import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Select } from "antd";
import { Copy, Download, Filter, History, ShieldCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { artifactTypeLabel } from "../../i18n";
import { request, type EvidenceRecord } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { Inspector, type InspectorTab } from "../../shared/ui/Inspector";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { EvidenceSummary } from "./EvidenceSummary";
import { assertProjectScope, useProjectId } from "../../entities/project/api";

const kinds = ["", "journey", "plan-gate", "candidate", "diff", "verification", "candidate-gate", "apply-receipt"];
type EvidenceVerificationRecord = components["schemas"]["EvidenceVerificationRecord"];

export function EvidencePage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  const client = useQueryClient();
  const [deliveryFilter, setDeliveryFilter] = useState(() => searchParams.get("delivery_id") ?? "");
  const [kindFilter, setKindFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<EvidenceRecord>();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [copyNotice, setCopyNotice] = useState("");
  const evidence = useQuery({ queryKey: ["evidence", projectId], queryFn: async ({ signal }) => assertProjectScope(projectId, await request<EvidenceRecord[]>(`/v1/evidence?project_id=${encodeURIComponent(projectId)}`, { signal }), "证据账本"), refetchInterval: 2500 });
  const verificationHistory = useQuery({ queryKey: ["evidence-verifications", selected?.id], enabled: Boolean(selected), queryFn: ({ signal }) => request<EvidenceVerificationRecord[]>(`/v1/evidence/${selected?.id}/verifications`, { signal }) });
  const verify = useMutation({ mutationFn: (id: string) => request<EvidenceRecord>(`/v1/evidence/${id}/verify`, { method: "POST" }), onSuccess: async (record) => { setSelected(record); await Promise.all([client.invalidateQueries({ queryKey: ["evidence", projectId] }), client.invalidateQueries({ queryKey: ["evidence-verifications", record.id] })]); } });
  const filtered = useMemo(() => (evidence.data ?? []).filter((item) =>
    (!deliveryFilter || item.delivery_id.includes(deliveryFilter.trim())) &&
    (!kindFilter || item.kind === kindFilter) &&
    (!statusFilter || item.status === statusFilter),
  ), [deliveryFilter, evidence.data, kindFilter, statusFilter]);
  useEffect(() => {
    setSelected(undefined);
    setInspectorOpen(false);
    setDeliveryFilter(searchParams.get("delivery_id") ?? "");
    setCopyNotice("");
  }, [projectId, searchParams]);

  if (evidence.isLoading) return <LoadingState label="正在读取不可变证据账本…"/>;
  if (evidence.error) return <ErrorState error={evidence.error} retry={() => evidence.refetch()}/>;
  const records = evidence.data ?? [];
  const tabs: InspectorTab[] = selected ? [
    { id: "summary", label: "摘要", content: <><h3>来源与生产身份</h3><dl className="definition-list"><dt>项目</dt><dd>{selected.project_id}</dd><dt>交付</dt><dd>{selected.delivery_id}</dd><dt>类型</dt><dd>{artifactTypeLabel(selected.kind)}</dd><dt>来源</dt><dd>{selected.source_kind} / {selected.source_id}</dd><dt>生产身份</dt><dd>{selected.producer_identity}</dd><dt>内容 SHA-256</dt><dd><code>{selected.content_sha256 ?? "未生成"}</code></dd><dt>验证时间</dt><dd>{selected.verified_at ?? "尚未验证"}</dd></dl>{selected.verification_error && <div className="repair-callout"><b>验证失败</b><span>{selected.verification_error}</span></div>}<h3>载荷预览</h3><pre className="code-block">{JSON.stringify(selected.payload, null, 2)}</pre><h3>最近验证</h3>{verificationHistory.isLoading ? <LoadingState label="正在读取验证历史…"/> : verificationHistory.data?.length ? <p>{verificationHistory.data[0].error ?? "哈希与当前不可变内容一致"}</p> : <p className="muted">尚无重新验证记录。</p>}</> },
    { id: "payload", label: "载荷", content: <><h3>结构化载荷</h3><pre className="code-block">{JSON.stringify(selected.payload, null, 2)}</pre></> },
    { id: "verification", label: "验证历史", content: <><section className="verification-history"><div className="panel-subtitle"><History size={15}/>重新验证历史</div>{verificationHistory.isLoading ? <LoadingState label="正在读取验证历史…"/> : verificationHistory.error ? <ErrorState error={verificationHistory.error} retry={() => verificationHistory.refetch()}/> : verificationHistory.data?.length ? verificationHistory.data.map((item) => <article key={item.id}><StatusBadge value={item.status}/><div><b>{new Date(item.verified_at).toLocaleString("zh-CN")}</b><small>{item.error ?? "哈希与当前不可变内容一致"}</small></div><code>{item.id.slice(0, 12)}</code></article>) : <EmptyState title="尚无重新验证记录" detail="初始证据状态来自入账校验；重新验证后会在此追加记录。"/>}</section>{verify.error && <ErrorState error={verify.error}/>}</> },
  ] : [];

  return <div className="evidence-page">
    <section className="page-heading">
      <p className="eyebrow">不可变完整性账本 · 真实数据</p>
      <h2>从目录扫描，到载荷深查。</h2>
      <p>先用类型、完整性和交付标识缩小范围，再在按需检查器中核对来源、SHA-256 与重新验证历史。</p>
    </section>

    <EvidenceSummary records={records}/>

    <section className="evidence-filters surface-card">
      <Filter size={17} aria-hidden="true"/>
      <label className="field"><span>交付 ID</span><Input aria-label="按交付筛选" placeholder="搜索 delivery id" value={deliveryFilter} onChange={(event) => setDeliveryFilter(event.target.value)}/></label>
      <label className="field"><span>证据类型</span><Select aria-label="按证据类型筛选" value={kindFilter} onChange={setKindFilter} options={kinds.map((kind) => ({ value: kind, label: kind ? artifactTypeLabel(kind) : "全部类型" }))}/></label>
      <label className="field"><span>完整性</span><Select aria-label="按完整性筛选" value={statusFilter} onChange={setStatusFilter} options={[{ value: "", label: "全部完整性" }, { value: "verified", label: "已验证" }, { value: "invalid", label: "无效" }, { value: "unavailable", label: "不可用" }]}/></label>
    </section>

    <section className="surface-card evidence-directory">
      <div className="panel-head"><div><span>证据目录</span><small>{filtered.length} / {records.length} 条 · 项目 {projectId}</small></div><small>选择一行打开 420px 完整性检查器</small></div>
      {filtered.length === 0 ? <EmptyState title="没有符合条件的真实证据" detail="清除筛选，或先完成交付阶段。系统不会补造缺失证据。"/> :
        <div className="evidence-table" role="list">{filtered.map((item) => <Button type="text" key={item.id} className={selected?.id === item.id && inspectorOpen ? "selected" : ""} onClick={() => { setSelected(item); setInspectorOpen(true); setCopyNotice(""); }}>
          <span><strong>{artifactTypeLabel(item.kind)}</strong><small>{item.id} · {item.delivery_id} · {item.producer_identity}</small></span>
          <span><strong>{item.source_kind}</strong><small>{item.source_id}</small></span>
          <StatusBadge value={item.status}/>
          <span className="evidence-hash"><code>{item.content_sha256?.slice(0, 16) ?? "无哈希"}</code><small>{item.verified_at ?? "尚未验证"}</small></span>
        </Button>)}</div>}
    </section>

    {selected && <Inspector
      open={inspectorOpen}
      kicker={`${artifactTypeLabel(selected.kind)} · ${selected.status}`}
      title={selected.id}
      tabs={tabs}
      onClose={() => setInspectorOpen(false)}
      footer={<div className="inspector-actions"><Button icon={<ShieldCheck size={16}/>} loading={verify.isPending} onClick={() => verify.mutate(selected.id)}>重新验证</Button><Button icon={<Copy size={16}/>} disabled={!selected.content_sha256} onClick={async () => {
        if (!selected.content_sha256) return;
        try {
          await navigator.clipboard.writeText(selected.content_sha256);
          setCopyNotice("内容哈希已复制");
        } catch {
          setCopyNotice("浏览器未授权读取剪贴板，请从摘要字段手动复制内容哈希。");
        }
      }}>复制内容哈希</Button><Button icon={<Download size={16}/>} onClick={() => downloadEvidence(selected)}>导出 JSON</Button>{copyNotice && <p className="field-help" role="status">{copyNotice}</p>}</div>}
    />}
  </div>;
}

function downloadEvidence(record: EvidenceRecord) {
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `evidence-${record.id}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
