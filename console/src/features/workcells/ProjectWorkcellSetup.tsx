import { useState } from "react";
import { Button, Input, Select } from "antd";
import { CheckCircle2, GitBranch, KeyRound, Network, ShieldCheck } from "lucide-react";
import { ApiProblem } from "../../shared/api/client";
import { ErrorState, LoadingState } from "../../shared/feedback/AsyncState";
import { StatusBadge } from "../../shared/ui/StatusBadge";
import { WorkspaceVerificationProfilePanel } from "./WorkspaceVerificationProfilePanel";
import {
  useActivateProjectTeam,
  useCreateWorkspaceBinding,
  useProjectWorkcells,
  useVerifyWorkspaceBinding,
} from "./api";

type BindingDraft = {
  adapterType: "external-git" | "managed-bare-git";
  repositoryUri: string;
  credentialReference: string;
};

export function ProjectWorkcellSetup({ projectId }: { projectId: string }) {
  const topology = useProjectWorkcells(projectId);
  const create = useCreateWorkspaceBinding(projectId);
  const verify = useVerifyWorkspaceBinding(projectId);
  const activate = useActivateProjectTeam(projectId);
  const [drafts, setDrafts] = useState<Record<string, BindingDraft>>({});

  if (topology.isLoading) return <section className="panel"><LoadingState label="正在读取 Workcell 与仓库绑定…"/></section>;
  if (topology.error) {
    if (topology.error instanceof ApiProblem && topology.error.problem.code === "PROJECT_TEAM_BINDING_NOT_FOUND") return null;
    return <section className="panel"><ErrorState error={topology.error} retry={() => topology.refetch()}/></section>;
  }
  if (!topology.data) return null;
  const data = topology.data;
  if (
    !Array.isArray(data.workspace_bindings)
    || !Array.isArray(data.workcell_bindings)
    || !Array.isArray(data.team_revision?.workcells)
  ) return null;
  const workspaceById = new Map(data.workspace_bindings.map((item) => [item.id, item]));
  const bindingByKey = new Map(data.workcell_bindings.map((item) => [item.workcell_key, workspaceById.get(item.workspace_binding_id)]));
  const allReady = data.team_revision.workcells.every((item) => {
    const workspace = bindingByKey.get(item.workcell_key);
    return workspace?.status === "ready" && Boolean(workspace.verification_profile);
  });
  const canConfigure = data.team_binding.status === "provisioning";
  const pendingError = create.error ?? verify.error ?? activate.error;
  const updateDraft = (key: string, update: Partial<BindingDraft>) => setDrafts((current) => ({ ...current, [key]: { adapterType: current[key]?.adapterType ?? "external-git", repositoryUri: current[key]?.repositoryUri ?? "", credentialReference: current[key]?.credentialReference ?? "", ...update } }));

  return <section className="panel project-workcell-setup">
    <div className="workcell-setup-head"><div><span className="eyebrow">Repository Isolation Contract</span><h2>{data.team_revision.name}</h2><p>每个角色只绑定一个可写 Primary Repository。其他 Workcell 的输入只能以内容寻址 Artifact 进入。</p></div><div className="team-state"><StatusBadge value={data.team_binding.status}/><code>{data.team_revision.template_id}:R{data.team_revision.revision}</code></div></div>
    <div className="workspace-cassette-grid">{data.team_revision.workcells.map((workcell) => {
      const workspace = bindingByKey.get(workcell.workcell_key);
      const draft = drafts[workcell.workcell_key] ?? { adapterType: "external-git", repositoryUri: "", credentialReference: "" };
      const repositoryUri = draft.adapterType === "managed-bare-git" ? `projects/${projectId}/${workcell.workcell_key}` : draft.repositoryUri;
      return <article className={`workspace-cassette workspace-${workspace?.status ?? "unbound"}`} key={workcell.workcell_key}>
        <header><span><GitBranch size={16}/><b>{workcell.name}</b></span><StatusBadge value={workspace?.status ?? "unbound"}/></header>
        <code>{workcell.workcell_key}</code><p>{workcell.responsibility}</p>
        {workspace ? <div className="workspace-receipt"><span>Primary Repository</span><code>{workspace.repository_uri}</code><dl><dt>Adapter</dt><dd>{workspace.adapter_type}</dd><dt>Verification</dt><dd>{workspace.verification_sha256?.slice(0, 12) ?? workspace.error_code ?? "等待验证"}</dd><dt>直接推进 main</dt><dd>{workspace.verification?.direct_fast_forward_main === true ? "允许" : "尚未证明"}</dd></dl>{canConfigure && workspace.status !== "ready" && <Button type="primary" icon={<ShieldCheck size={14}/>} loading={verify.isPending} onClick={() => verify.mutate({ workspaceId: workspace.id, expectedVersion: workspace.version })}>验证仓库能力</Button>}</div> : canConfigure ? <div className="workspace-binding-form"><label>Adapter<Select value={draft.adapterType} onChange={(value) => updateDraft(workcell.workcell_key, { adapterType: value })} options={[{ value: "external-git", label: "GitHub HTTPS（Live）" }, { value: "managed-bare-git", label: "Managed Bare Git（Deterministic）" }]}/></label><label>Repository URI<Input value={repositoryUri} disabled={draft.adapterType === "managed-bare-git"} placeholder="https://github.com/org/private-repo.git" onChange={(event) => updateDraft(workcell.workcell_key, { repositoryUri: event.target.value })}/></label>{draft.adapterType === "external-git" && <label><span><KeyRound size={12}/>Credential Reference</span><Input value={draft.credentialReference} placeholder="env://AGENT_TEAM_OS_GITHUB_TOKEN" onChange={(event) => updateDraft(workcell.workcell_key, { credentialReference: event.target.value })}/><small>只保存 env:// 或 keychain:// 引用；不要粘贴 Token。</small></label>}<Button icon={<Network size={14}/>} disabled={!repositoryUri || (draft.adapterType === "external-git" && !draft.credentialReference)} loading={create.isPending} onClick={() => create.mutate({ workcell_key: workcell.workcell_key, kind: "git_repository_v1", adapter_type: draft.adapterType, repository_uri: repositoryUri, credential_reference: draft.adapterType === "external-git" ? draft.credentialReference : null })}>绑定 Primary Repository</Button></div> : <div className="workspace-empty">兼容投影中没有该 Workcell 的仓库；历史项目继续按旧 Journey 运行。</div>}
        {workspace && <WorkspaceVerificationProfilePanel projectId={projectId} workspace={workspace}/>}
      </article>;
    })}</div>
    {pendingError && (
      <ErrorState error={pendingError}/>
    )}
    <footer className="workcell-activation-bar"><span>{allReady ? <><CheckCircle2 size={16}/>四个独立 Repository 均已验证，可冻结项目 Team Binding。</> : <>逐仓绑定并验证后才能激活；重复 Repository URI 会被服务拒绝。</>}</span><Button type="primary" disabled={!canConfigure || !allReady} loading={activate.isPending} onClick={() => activate.mutate(data.team_binding.version)}>激活四仓团队</Button></footer>
  </section>;
}

export function repositoryIsolationSummary(repositoryUris: string[]) {
  return {
    repositoryCount: repositoryUris.length,
    uniqueRepositoryCount: new Set(repositoryUris).size,
    isolated: repositoryUris.length > 0 && new Set(repositoryUris).size === repositoryUris.length,
  };
}
