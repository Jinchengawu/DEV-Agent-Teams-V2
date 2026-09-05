from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.review_scope import (
    WorkcellReviewScope,
    compile_review_scope,
    product_review_policies,
)

WORKCELL_KEYS = ("design", "frontend", "backend", "qa")


def planning_payloads(acceptance_id: str = "AC-LOGIN") -> tuple[dict, dict]:
    requirements = {
        "summary": "登录交付",
        "acceptance_criteria": [{"id": acceptance_id, "statement": "用户可以完成登录"}],
    }
    responsibilities = {
        "design": "记录登录的页面交互和错误状态规范",
        "frontend": "实现登录页面交互和错误状态",
        "backend": "实现登录认证接口和错误响应",
        "qa": "验证登录跨层流程并记录结果",
    }
    task = {
        "title": "登录交付",
        "instructions": "按批准职责实现登录",
        "acceptance_ids": [acceptance_id],
        "workcell_acceptance": [
            {
                "workcell_key": key,
                "acceptance": [{"acceptance_id": acceptance_id, "responsibility": responsibility}],
            }
            for key, responsibility in responsibilities.items()
        ],
    }
    return requirements, task


def review_scope(
    workcell_key: str = "frontend", acceptance_id: str = "AC-LOGIN"
) -> WorkcellReviewScope:
    requirements, task = planning_payloads(acceptance_id)
    return compile_review_scope(
        requirements=requirements,
        task=task,
        plan_subject_sha256=sha256_json({"requirements": requirements, "task": task}),
        plan_approved=True,
        workcell_key=workcell_key,
        required_workcells=WORKCELL_KEYS,
        policies=product_review_policies(WORKCELL_KEYS),
    )
