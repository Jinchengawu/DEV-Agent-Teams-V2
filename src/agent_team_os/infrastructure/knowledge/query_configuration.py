"""非秘密本地运行预算配置；缺失保持未配置，不推导模型能力。"""
from __future__ import annotations

import os
from pathlib import Path

from ...modules.knowledge.query_admission import PreparationQueryBudget


def preparation_query_budget_from_environment() -> PreparationQueryBudget | None:
    configured = os.environ.get("AGENT_TEAM_OS_QUERY_BUDGET_FILE")
    if not configured:
        return None
    path = Path(configured)
    try:
        if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
                or path.stat().st_size > 65_536):
            return None
        return PreparationQueryBudget.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None
