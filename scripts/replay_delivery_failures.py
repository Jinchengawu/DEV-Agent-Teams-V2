#!/usr/bin/env python3
"""回放版本化的交付失败案例，输出离线合同回归报告。"""

from __future__ import annotations

from agent_team_os.failure_regression import run_failure_regression_cli

if __name__ == "__main__":
    raise SystemExit(run_failure_regression_cli())
