const statuses: Record<string, string> = {
  queued: "已排队",
  planning: "规划中",
  awaiting_plan_decision: "等待计划审批",
  awaiting_design_decision: "等待设计审批",
  executing: "执行中",
  verifying: "验证中",
  awaiting_candidate_decision: "等待候选审批",
  applying: "应用中",
  completed: "已完成",
  rejected: "已拒绝",
  failed: "失败",
  cancelled: "已取消",
  unknown: "未知",
  ready: "就绪",
  valid: "校验通过",
  invalid: "校验失败",
  passed: "通过",
  verified: "已验证",
  unavailable: "不可用",
  provisioning: "初始化中",
  active: "可运行",
  provision_failed: "初始化失败",
  archived: "已归档",
  backlog: "待规划",
  "plan-approval": "计划审批",
  "design-approval": "设计审批",
  "candidate-approval": "候选审批",
  "failed-cancelled": "失败 / 取消",
  default: "默认",
  enabled: "已授权",
  disabled: "未授权",
  qualified: "资格通过",
  delegating: "委派中",
  reviewing: "审查中",
  synthesizing: "结果合成中",
  succeeded: "已成功",
  interrupted: "已中断",
  timed_out: "已超时",
  pending: "等待中",
  unbound: "未绑定",
  legacy_projected: "历史兼容投影",
  healthy: "发布健康",
  release_drifted: "发布已部分推进",
  needs_attention: "需要人工处理",
  applied: "已推进 main",
  draft: "草稿",
  open: "开放",
  closed: "已关闭",
  merged: "已合并",
};

const commands: Record<string, string> = {
  "approve-plan": "批准计划",
  "reject-plan": "拒绝计划",
  "approve-design": "批准设计",
  "reject-design": "拒绝设计",
  "accept-candidate": "接受候选",
  "reject-candidate": "拒绝候选",
  cancel: "取消交付",
};

const artifactTypes: Record<string, string> = {
  manual: "手工文档",
  requirement: "需求",
  task: "任务合同",
  candidate: "候选变更",
  verification: "机器验证",
  "plan-gate": "计划审批",
  "design-gate": "设计审批",
  "candidate-gate": "候选审批",
  "release-bundle": "全栈发布包",
  "release-manifest": "发布清单",
  "apply-receipt": "应用回执",
  "journey-revision": "旅程版本",
  journey: "旅程快照",
  diff: "代码差异",
};

const journeySteps: Record<string, string> = {
  requirements: "需求分析",
  tasking: "任务规划",
  "approve-plan": "计划审批",
  design: "UI 设计",
  "approve-design": "设计审批",
  "implementation-repair": "前后端实现与测试",
  "approve-release": "发布审批",
  delivery: "代码交付",
  "approve-candidate": "候选审批",
};

export function statusLabel(value: string): string {
  return statuses[value] ?? value;
}

export function commandLabel(value: string): string {
  return commands[value] ?? value;
}

export function artifactTypeLabel(value: string): string {
  return artifactTypes[value] ?? value;
}

export function documentTitle(title: string, artifactType: string): string {
  const suffix = ` · ${artifactType}`;
  return title.endsWith(suffix)
    ? `${title.slice(0, -suffix.length)} · ${artifactTypeLabel(artifactType)}`
    : title;
}

export function journeyStepLabel(value: string): string {
  return journeySteps[value] ?? value;
}

export function identityLabel(value?: string): string {
  if (!value) return "未获取身份";
  if (value === "codex-simulated-hermes") return "Codex 模拟 Hermes";
  if (value === "codex-cli") return "Codex 命令行";
  if (value === "deterministic-test") return "确定性测试身份";
  return value;
}

export function runtimeTypeLabel(value: string): string {
  if (value === "codex-cli") return "Codex 命令行";
  if (value === "hermes-http") return "Hermes HTTP 服务";
  if (value === "hermes-acp") return "Hermes ACP 服务";
  return value;
}

export function repositoryRoleLabel(value: string): string {
  if (value === "backend") return "后端";
  if (value === "design") return "UI 设计";
  if (value === "frontend") return "前端";
  if (value === "qa") return "测试审查";
  return value;
}

export function httpErrorLabel(status: number): string {
  if (status === 409) return "当前数据已变化或操作与当前状态冲突，请刷新后重试。";
  if (status === 404) return "未找到请求的数据，请刷新页面后重试。";
  if (status === 422) return "输入内容不符合要求，请检查后重试。";
  if (status === 503) return "运行依赖尚未就绪，请先完成环境检查。";
  return `请求失败，状态码 ${status}。`;
}
