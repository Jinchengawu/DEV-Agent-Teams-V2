import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Button, Input, Select } from "antd";
import { ArrowRight, RefreshCw } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { statusLabel } from "../../i18n";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { projectPath, useProject, useProjectId } from "../../entities/project/api";
import { useCreateDelivery, useDeliveries, useDeliveryPipelines } from "./api";

const defaultRequest = "增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。";

export function DeliveriesPage() {
  const navigate = useNavigate();
  const projectId = useProjectId();
  const project = useProject(projectId);
  const deliveries = useDeliveries(projectId);
  const pipelines = useDeliveryPipelines();
  const activePipelines = useMemo(
    () => project.data?.pipeline_bindings
      .filter((binding) => binding.enabled)
      .map((binding) => ({
        id: binding.pipeline_id,
        name: pipelines.data?.find((pipeline) => pipeline.id === binding.pipeline_id)?.name ?? binding.pipeline_id,
        active_revision: binding.pipeline_revision,
      })) ?? [],
    [pipelines.data, project.data?.pipeline_bindings],
  );
  const [requestText, setRequestText] = useState(defaultRequest);
  const [pipelineRevisionId, setPipelineRevisionId] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [problem, setProblem] = useState("");
  const create = useCreateDelivery(projectId, (id) => navigate(projectPath(projectId, `deliveries/${id}`)));

  useEffect(() => {
    setPipelineRevisionId("");
    setReviewing(false);
    setProblem("");
  }, [projectId]);

  useEffect(() => {
    if (!pipelineRevisionId && activePipelines[0]?.active_revision) {
      setPipelineRevisionId(`${activePipelines[0].id}:${activePipelines[0].active_revision}`);
    }
  }, [activePipelines, pipelineRevisionId]);

  const selectedPipeline = activePipelines.find(
    (pipeline) => `${pipeline.id}:${pipeline.active_revision}` === pipelineRevisionId,
  );
  const workcellProject = project.data?.workspace.repository_ref?.startsWith("workspace-set/") ?? false;

  const review = (event: FormEvent) => {
    event.preventDefault();
    if (!requestText.trim()) {
      setProblem("请输入边界清晰的交付目标。");
      document.getElementById("delivery-goal")?.focus();
      return;
    }
    if (!pipelineRevisionId) {
      setProblem("请选择一个已经激活的 Pipeline Revision。");
      document.getElementById("delivery-pipeline")?.focus();
      return;
    }
    setProblem("");
    setReviewing(true);
  };

  const recent = [...(deliveries.data ?? [])].sort((left, right) =>
    String(right.updated_at ?? right.created_at ?? "").localeCompare(String(left.updated_at ?? left.created_at ?? "")),
  );

  if (project.isLoading) return <LoadingState label="正在读取项目执行边界…"/>;
  if (project.error) return <ErrorState error={project.error} retry={() => project.refetch()}/>;

  return <div className="delivery-home">
    <section className="page-heading delivery-home-heading">
      <p className="eyebrow">{project.data?.project.name ?? projectId} · 真实控制面</p>
      <h2>从一句交付目标，进入可审批的工程流程。</h2>
      <p>描述边界、选择已发布 Pipeline，然后在同一工作面跟进计划、验证、候选与不可变证据。</p>
    </section>

    <div className="workbench-grid">
      <article className="delivery-thesis">
        <div><p className="eyebrow">交付，而不是聊天</p><h2>每一步都有<br/>责任主体、证据<br/>与人工闸门。</h2><p>多 Agent 协作被压缩成可解释的交付运行：负责人始终知道现在在哪、发生了什么、下一步由谁决定。</p></div>
        <ol>
          <li><b>01</b><span>定义边界<small>需求与验收</small></span></li>
          <li><b>02</b><span>人工审批<small>计划与候选</small></span></li>
          <li><b>03</b><span>验证证据<small>不可变标识</small></span></li>
        </ol>
      </article>

      <form className="delivery-composer surface-card" onSubmit={review} noValidate>
        <div className="composer-head"><div><p className="eyebrow">新建交付</p><h2>发起一次交付</h2></div><span className="source-badge">真实 API</span></div>
        <label className="field"><span>交付目标</span><Input.TextArea id="delivery-goal" aria-label="交付目标" value={requestText} aria-invalid={Boolean(problem && !requestText.trim())} onChange={(event) => { setRequestText(event.target.value); setReviewing(false); setProblem(""); }} placeholder="描述要交付的结果、边界与验收要求" autoSize={{ minRows: 5, maxRows: 10 }}/></label>

        <div className="composer-config">
          <label className="field"><span>执行 Pipeline</span><Select id="delivery-pipeline" aria-label="执行 Pipeline" value={pipelineRevisionId || undefined} placeholder="请选择已激活版本" onChange={(value) => { setPipelineRevisionId(value); setReviewing(false); setProblem(""); }} options={activePipelines.map((pipeline) => ({ value: `${pipeline.id}:${pipeline.active_revision}`, label: `${pipeline.name} · R${pipeline.active_revision}` }))}/></label>
          <div className="readonly-field"><span>项目工作区</span><b>{workcellProject ? "Repository Workcell Set" : project.data?.workspace.workspace_id ?? projectId}</b><small>{workcellProject ? "每个 Workcell 独占 Primary Repository；跨 Workcell 只传 Artifact" : "独立 Git Main，候选应用受 CAS 保护"}</small></div>
          <div className="readonly-field"><span>Agent 授权</span><b>{workcellProject ? "Pipeline 冻结 Slot" : `${project.data?.deployment_access.filter((access) => access.enabled).length ?? 0} 个 Deployment`}</b><small>{workcellProject ? "Provider Binding 已在 Published Revision 预解析" : "创建时与 Pipeline Revision 一并冻结"}</small></div>
        </div>

        {pipelines.error && <ErrorState error={pipelines.error} retry={() => pipelines.refetch()}/>}
        {pipelines.isSuccess && activePipelines.length === 0 && <div className="inline-guidance"><b>没有已激活 Pipeline</b><span>先到“可视化编排”发布并激活一个通过校验的 Revision。</span></div>}
        {problem && <p className="field-error" role="alert">{problem}</p>}

        {!reviewing ? <div className="composer-actions"><span>提交后先检查边界，再创建真实交付运行。</span><Button className="primary" htmlType="submit" disabled={pipelines.isLoading || activePipelines.length === 0}>生成交付计划</Button></div> :
          <section className="delivery-confirmation" aria-label="确认交付边界">
            <div><span className="eyebrow">提交前确认</span><h3>目标与执行边界</h3></div>
            <dl><dt>目标</dt><dd>{requestText.trim()}</dd><dt>Pipeline</dt><dd>{selectedPipeline?.name} · R{selectedPipeline?.active_revision}</dd><dt>执行方式</dt><dd>{workcellProject ? "四个隔离 Repository Workcell · 计划 / 设计 / 发布 Gate" : "隔离工作区 · 计划与候选双审批"}</dd></dl>
            <div className="confirm-actions"><Button className="secondary" onClick={() => { setReviewing(false); document.getElementById("delivery-goal")?.focus(); }}>继续编辑</Button><Button className="primary" loading={create.isPending} onClick={() => create.mutate({ userRequest: requestText.trim(), pipelineRevisionId })}>{create.isPending ? "正在创建交付…" : "确认并启动"}</Button></div>
          </section>}
        {create.error && <ErrorState error={create.error}/>}
      </form>
    </div>

    <section className="recent-runs">
      <div className="section-head"><div><p className="eyebrow">运行上下文</p><h2>最近运行</h2><p>继续最近一次交付，不重新寻找分散页面。</p></div><Button className="quiet-button" icon={<RefreshCw size={15}/>} onClick={() => deliveries.refetch()}>刷新</Button></div>
      <div className="run-list surface-card">
        {deliveries.isLoading && <LoadingState label="正在读取真实交付历史…"/>}
        {deliveries.error && <ErrorState error={deliveries.error} retry={() => deliveries.refetch()}/>}
        {deliveries.isSuccess && recent.length === 0 && <EmptyState title="当前还没有交付运行" detail="上方确认一个边界清晰的目标后，系统才会创建真实运行。"/>}
        {recent.map((delivery) => <Button type="text" key={delivery.id} className="run-row" onClick={() => navigate(projectPath(projectId, `deliveries/${delivery.id}`))}>
          <time dateTime={delivery.updated_at ?? delivery.created_at}>{formatRunTime(delivery.updated_at ?? delivery.created_at)}</time>
          <span><strong>{delivery.user_request}</strong><small>{runSummary(delivery.status)} · {delivery.id.slice(0, 8)} · v{delivery.version}</small></span>
          <StatusBadge value={delivery.status}/>
          <ArrowRight size={17} aria-hidden="true"/>
        </Button>)}
      </div>
    </section>
  </div>;
}

function formatRunTime(value?: string): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function runSummary(status: string): string {
  const summaries: Record<string, string> = {
    queued: "已排队，由规划身份接手",
    planning: "正在收敛需求与计划",
    awaiting_plan_decision: "等待负责人审批计划",
    executing: "Codex CLI 正在隔离执行",
    verifying: "正在运行固定机器验证",
    awaiting_candidate_decision: "候选与证据等待审批",
    applying: "正在执行受控原子应用",
    completed: "审批、验证与应用均已记录",
    failed: "运行失败，主分支保持受保护",
    rejected: "人工拒绝，候选未应用",
    cancelled: "运行已取消",
    cancelling: "正在清理运行，完成前保留项目占用",
    needs_attention: "发布需要恢复，项目仍被占用",
  };
  return summaries[status] ?? statusLabel(status);
}
