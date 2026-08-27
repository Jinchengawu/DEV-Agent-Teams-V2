import { useCallback, useMemo, useState } from "react";
import { Copy, Filter, ShieldCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { artifactTypeLabel, statusLabel } from "../../i18n";
import { request, type EvidenceRecord } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { Inspector, type InspectorTab } from "../../shared/ui/Inspector";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { EvidenceSummary } from "./EvidenceSummary";

const kinds = ["", "journey", "plan-gate", "candidate", "diff", "verification", "candidate-gate", "apply-receipt"];

export function EvidencePage() {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<EvidenceRecord>();
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [copyLabel, setCopyLabel] = useState("复制内容哈希");
  const closeInspector = useCallback(() => { setInspectorOpen(false); setCopyLabel("复制内容哈希"); }, []);
  const evidence = useQuery({ queryKey: ["evidence"], queryFn: () => request<EvidenceRecord[]>("/v1/evidence"), refetchInterval: 2500 });
  const verify = useMutation({
    mutationFn: (id: string) => request<EvidenceRecord>(`/v1/evidence/${id}/verify`, { method: "POST" }),
    onSuccess: async (record) => {
      setSelected(record);
      await client.invalidateQueries({ queryKey: ["evidence"] });
    },
  });
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (evidence.data ?? []).filter((item) =>
      (!query || `${item.delivery_id} ${item.id} ${item.content_sha256 ?? ""} ${item.source_id} ${item.producer_identity}`.toLowerCase().includes(query)) &&
      (!kindFilter || item.kind === kindFilter) &&
      (!statusFilter || item.status === statusFilter),
    );
  }, [evidence.data, kindFilter, search, statusFilter]);

  if (evidence.isLoading) return <LoadingState label="正在读取不可变证据账本…"/>;
  if (evidence.error) return <ErrorState error={evidence.error} retry={() => evidence.refetch()}/>;
  const records = evidence.data ?? [];

  const openRecord = (record: EvidenceRecord) => {
    setSelected(record);
    setInspectorOpen(true);
    setCopyLabel("复制内容哈希");
  };

  const copyHash = async () => {
    if (!selected?.content_sha256) return;
    try {
      await navigator.clipboard.writeText(selected.content_sha256);
      setCopyLabel("内容哈希已复制");
    } catch {
      setCopyLabel("浏览器未授权复制");
    }
  };

  const tabs: InspectorTab[] = selected ? [
    { id: "summary", label: "摘要", content: <><h3>来源与生产身份</h3><dl className="definition-list"><dt>交付</dt><dd>{selected.delivery_id}</dd><dt>类型</dt><dd>{artifactTypeLabel(selected.kind)}</dd><dt>来源</dt><dd>{selected.source_kind} / {selected.source_id}</dd><dt>生产身份</dt><dd>{selected.producer_identity}</dd><dt>创建时间</dt><dd>{formatDateTime(selected.created_at)}</dd><dt>SHA-256</dt><dd><code>{selected.content_sha256 ?? "未生成"}</code></dd></dl></> },
    { id: "payload", label: "载荷", content: <><h3>结构化载荷</h3><pre className="code-block">{JSON.stringify(selected.payload ?? {}, null, 2)}</pre></> },
    { id: "verification", label: "验证", content: <><h3>完整性：{statusLabel(selected.status)}</h3><dl className="definition-list"><dt>验证时间</dt><dd>{formatDateTime(selected.verified_at ?? undefined)}</dd><dt>验证错误</dt><dd>{selected.verification_error ?? "无"}</dd></dl>{verify.error && <ErrorState error={verify.error}/>}</> },
  ] : [];

  return <div className="evidence-page">
    <section className="page-heading">
      <p className="eyebrow">不可变完整性账本 · 真实数据</p>
      <h2>从目录扫描，到载荷深查。</h2>
      <p>先用类型、完整性和交付标识缩小范围，再在按需检查器中核对来源、完整 SHA-256 与结构化载荷。</p>
    </section>

    <EvidenceSummary records={records}/>

    <section className="evidence-filters surface-card">
      <Filter size={17} aria-hidden="true"/>
      <label className="field"><span>交付、证据或哈希</span><input aria-label="按交付、证据或哈希筛选" placeholder="搜索 delivery、evidence 或 sha256" value={search} onChange={(event) => setSearch(event.target.value)}/></label>
      <label className="field"><span>证据类型</span><select aria-label="按证据类型筛选" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>{kinds.map((kind) => <option key={kind} value={kind}>{kind ? artifactTypeLabel(kind) : "全部类型"}</option>)}</select></label>
      <label className="field"><span>完整性</span><select aria-label="按完整性筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部完整性</option><option value="verified">已验证</option><option value="invalid">无效</option><option value="unavailable">不可用</option></select></label>
    </section>

    <section className="surface-card evidence-directory">
      <div className="panel-head"><div><span>证据目录</span><small>{filtered.length} / {records.length} 条</small></div><small>选择一行打开完整性检查器</small></div>
      {filtered.length === 0 ? <EmptyState title="没有符合条件的真实证据" detail="清除筛选，或先完成交付阶段。系统不会补造缺失证据。"/> :
        <div className="evidence-table" role="list">{filtered.map((item) => <button key={item.id} type="button" className={selected?.id === item.id && inspectorOpen ? "selected" : ""} onClick={() => openRecord(item)}>
          <span><strong>{artifactTypeLabel(item.kind)}</strong><small>{item.id} · 交付 {item.delivery_id.slice(0, 12)}</small></span>
          <span><strong>{item.producer_identity}</strong><small>{item.source_kind} / {item.source_id}</small></span>
          <StatusBadge value={item.status}/>
          <span className="evidence-hash"><code>{item.content_sha256?.slice(0, 16) ?? "无哈希"}</code><small>{formatDateTime(item.created_at)}</small></span>
        </button>)}</div>}
    </section>

    {selected && <Inspector
      open={inspectorOpen}
      kicker={`${artifactTypeLabel(selected.kind)} · ${statusLabel(selected.status)}`}
      title={selected.id}
      tabs={tabs}
      onClose={closeInspector}
      footer={<><button className="secondary" disabled={!selected.content_sha256} onClick={copyHash}><Copy size={15}/>{copyLabel}</button><button className="secondary" disabled={verify.isPending} onClick={() => verify.mutate(selected.id)}><ShieldCheck size={15}/>{verify.isPending ? "正在重新验证…" : "重新验证当前证据"}</button></>}
    />}
  </div>;
}

function formatDateTime(value?: string): string {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}
