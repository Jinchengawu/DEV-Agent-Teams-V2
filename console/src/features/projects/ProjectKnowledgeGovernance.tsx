import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Select } from "antd";
import { BookLock, UsersRound } from "lucide-react";
import type { components } from "../../shared/api/generated/schema";
import { request } from "../../shared/api/client";
import { EmptyState, ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { useFeatureFlags } from "../../shared/features/api";
import { useIdentity } from "../identity/AuthGate";
import { tenantKnowledgeKeys, useTenantBindings } from "../knowledge/tenantApi";
import { projectKeys, type ProjectDetail } from "./api";

type User = components["schemas"]["User"];
type ProjectMembership = components["schemas"]["ProjectMembership"];
type ProjectMembershipUpdate = components["schemas"]["ProjectMembershipUpdate"];
type ProjectKnowledgeSourceApproval = components["schemas"]["ProjectKnowledgeSourceApproval"];
type ProjectKnowledgeSourceApprovalUpdate = components["schemas"]["ProjectKnowledgeSourceApprovalUpdate"];

export function ProjectKnowledgeGovernance({ detail }: { detail: ProjectDetail }) {
  const { user } = useIdentity();
  const projectId = detail.project.id;
  const queryClient = useQueryClient();
  const mutable = detail.project.lifecycle_status === "active" && user.role === "administrator";
  const featureFlags = useFeatureFlags();
  const tenantGovernanceEnabled = Boolean(featureFlags.data?.feishu_tenant_sync_v1);
  const memberships = useQuery({
    queryKey: ["projects", projectId, "memberships"],
    queryFn: ({ signal }) => request<ProjectMembership[]>(`/v1/projects/${encodeURIComponent(projectId)}/memberships`, { signal }),
  });
  const users = useQuery({
    queryKey: ["identity", "users", "project-governance"],
    queryFn: ({ signal }) => request<User[]>("/v1/users", { signal }),
    enabled: mutable,
  });
  const bindings = useTenantBindings(mutable && tenantGovernanceEnabled);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [selectedRole, setSelectedRole] = useState<ProjectMembershipUpdate["role"]>("viewer");

  useEffect(() => {
    if (!selectedUserId && users.data?.length) setSelectedUserId(users.data[0].id);
  }, [selectedUserId, users.data]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "memberships"] }),
      queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectId) }),
    ]);
  };
  const putMembership = useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: ProjectMembershipUpdate }) => request<ProjectMembership>(`/v1/projects/${encodeURIComponent(projectId)}/memberships/${encodeURIComponent(userId)}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: refresh,
  });
  const putApproval = useMutation({
    mutationFn: ({ bindingId, body }: { bindingId: string; body: ProjectKnowledgeSourceApprovalUpdate }) => request<ProjectKnowledgeSourceApproval>(`/v1/projects/${encodeURIComponent(projectId)}/knowledge-source-approvals/${encodeURIComponent(bindingId)}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
      await queryClient.invalidateQueries({ queryKey: tenantKnowledgeKeys.bindings });
    },
  });
  const operationError = putMembership.error ?? putApproval.error;
  const approvals = detail.knowledge_source_approvals ?? [];

  if (memberships.isLoading || featureFlags.isLoading || (mutable && (users.isLoading || (tenantGovernanceEnabled && bindings.isLoading)))) return <LoadingState label="正在读取项目成员与知识来源授权…"/>;
  if (memberships.error) return <ErrorState error={memberships.error} retry={() => memberships.refetch()}/>;
  if (users.error) return <ErrorState error={users.error} retry={() => users.refetch()}/>;
  if (featureFlags.error) return <ErrorState error={featureFlags.error} retry={() => featureFlags.refetch()}/>;
  if (bindings.error) return <ErrorState error={bindings.error} retry={() => bindings.refetch()}/>;

  return <section className="panel project-knowledge-governance evidence-rail">
    <div className="panel-head"><div><h2>成员与知识来源授权</h2><small>Global Role ∩ Project Role ∩ Approved Source Scope</small></div><StatusBadge value={mutable ? "mutable" : "read_only"}/></div>
    {!mutable && <div className="policy-note">当前身份只能读取授权投影；Tenant Connection 和 Approved Scope 由 Administrator 管理。</div>}
    {operationError && <ErrorState error={operationError}/>}
    <div className="project-knowledge-governance-grid">
      <article>
        <header><UsersRound size={18}/><div><h3>Project Membership</h3><p>Global Role 不直接替代项目成员关系；Owner、Editor、Viewer 各自只获得明确 Capability。</p></div></header>
        <div className="project-membership-list">{memberships.data?.map((membership) => {
          const principal = users.data?.find((candidate) => candidate.id === membership.user_id);
          return <div key={membership.user_id}><span><b>{principal?.display_name ?? membership.user_id}</b><code>{membership.user_id}</code></span><StatusBadge value={membership.role}/><small>v{membership.version}</small></div>;
        })}</div>
        {mutable && <div className="governance-inline-form">
          <Select aria-label="项目成员" value={selectedUserId || undefined} onChange={setSelectedUserId} options={users.data?.map((candidate) => ({ value: candidate.id, label: `${candidate.display_name} · ${candidate.role}` }))}/>
          <Select aria-label="项目角色" value={selectedRole} onChange={setSelectedRole} options={[{ value: "owner", label: "Owner" }, { value: "editor", label: "Editor" }, { value: "viewer", label: "Viewer" }]}/>
          <Button type="primary" disabled={!selectedUserId} loading={putMembership.isPending} onClick={() => {
            const current = memberships.data?.find((item) => item.user_id === selectedUserId);
            putMembership.mutate({ userId: selectedUserId, body: { role: selectedRole, ...(current ? { expected_version: current.version } : {}) } });
          }}>保存成员角色</Button>
        </div>}
      </article>

      <article>
        <header><BookLock size={18}/><div><h3>Approved Source Scope</h3><p>Project 只能使用这里批准的 Tenant Binding；RAG 权限必须单独打开。</p></div></header>
        {!tenantGovernanceEnabled && <EmptyState title="Gate A 尚未启用" detail="当前只保留已有授权投影；不会请求未挂载的 Tenant Binding API。"/>}
        {mutable && tenantGovernanceEnabled && (bindings.data?.length ?? 0) === 0 && <EmptyState title="没有可批准 Binding" detail="先在设置页完成 Tenant Connection、Space 探测与 Binding 冻结。"/>}
        <div className="project-source-approval-list">{(mutable && tenantGovernanceEnabled ? bindings.data ?? [] : approvals.map((approval) => ({ id: approval.binding_id, display_name: approval.binding_id, status: approval.enabled ? "ready" : "disabled", external_space_id: "approved-scope" }))).map((binding) => {
          const approval = approvals.find((item) => item.binding_id === binding.id);
          const enabled = Boolean(approval?.enabled);
          const ragEnabled = Boolean(approval?.rag_enabled);
          const update = (nextEnabled: boolean, nextRag: boolean) => putApproval.mutate({
            bindingId: binding.id,
            body: { enabled: nextEnabled, rag_enabled: nextRag, ...(approval ? { expected_version: approval.version } : {}) },
          });
          return <div key={binding.id}><span><b>{binding.display_name}</b><code>{binding.external_space_id}</code></span><div className="source-approval-state"><StatusBadge value={enabled ? "approved" : "not_approved"}/><StatusBadge value={ragEnabled ? "rag_enabled" : "rag_disabled"}/></div>{mutable && tenantGovernanceEnabled && <div className="resource-actions"><Button danger={enabled} loading={putApproval.isPending && putApproval.variables?.bindingId === binding.id} onClick={() => update(!enabled, !enabled ? true : false)}>{enabled ? "撤销来源" : "批准来源"}</Button>{enabled && <Button onClick={() => update(true, !ragEnabled)}>{ragEnabled ? "关闭 RAG" : "启用 RAG"}</Button>}</div>}</div>;
        })}</div>
      </article>
    </div>
  </section>;
}
