"""只读核对四仓 R2 的原生浏览器、Deterministic 与 Live 交接引用。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from agent_team_os.handoff_evidence import (
    build_handoff_evidence_index,
    write_handoff_evidence_index,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="产品仓库根目录。")
    parser.add_argument("--product-revision", required=True, help="拟交接的完整 Product SHA。")
    parser.add_argument("--acwm-revision", required=True, help="拟交接的完整 ACWM SHA。")
    parser.add_argument("--core-browser", type=Path, help="原生完整 R2 浏览器 JSON 收据。")
    parser.add_argument(
        "--deterministic-gate", type=Path, help="原生 Deterministic GateReport JSON。"
    )
    parser.add_argument("--live-release", type=Path, help="原生 V2 Live R2 JSON 报告。")
    parser.add_argument(
        "--output", type=Path, help="索引路径，必须位于产品 .agent-team-os/reports 内。"
    )
    arguments = parser.parse_args(argv)
    reports_root = (arguments.project_root / ".agent-team-os" / "reports").resolve()
    output = (arguments.output or reports_root / "delivery-handoff-index.json").resolve()
    if not output.is_relative_to(reports_root) or output == reports_root:
        parser.error("索引只能写入忽略的 .agent-team-os/reports 目录，不能改变已冻结产品文件。")
    sources = {
        role: value
        for role in ("core_browser", "deterministic_gate", "live_release")
        if (value := getattr(arguments, role)) is not None
    }
    if output in {path.resolve() for path in sources.values()}:
        parser.error("索引不能覆盖任何原生证据文件。")
    try:
        index = build_handoff_evidence_index(
            product_revision=arguments.product_revision,
            acwm_revision=arguments.acwm_revision,
            sources=sources,
        )
        write_handoff_evidence_index(output, index)
    except (OSError, ValueError):
        # 不打印输入报告的原始内容或 Pydantic input_value，避免传播非必要数据。
        parser.error("无法校验目标 Revision 或写入交接索引。")
    print(f"reference_check={index.reference_check}; issues={len(index.issues)}")
    print(f"index_sha256={index.index_sha256}")
    return 0 if index.reference_check == "consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
