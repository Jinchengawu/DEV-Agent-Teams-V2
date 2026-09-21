"""构建仅用于本地评测的 Agent-Team-OS Delivery Bundle。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from agent_team_os.delivery_bundle import BundleBuildError, build_delivery_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--git-revision")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="仅供开发验证；Manifest 会标记 development_only。",
    )
    arguments = parser.parse_args()
    project_root = arguments.project_root.resolve()
    revision = arguments.git_revision or subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    worktree_clean = not subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not worktree_clean and not arguments.allow_dirty:
        raise BundleBuildError(
            "工作树不干净；正式交付 Bundle 必须在已冻结 Revision 上构建。"
        )
    result = build_delivery_bundle(
        project_root=project_root,
        output_root=arguments.output_root,
        wheel=arguments.wheel,
        git_revision=revision,
        worktree_clean=worktree_clean,
    )
    print(f"bundle_root={result.bundle_root}")
    print(f"manifest_sha256={result.manifest_sha256}")
    print(
        "evidence_scope="
        + ("local_bundle_only" if worktree_clean else "development_only_dirty_worktree")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
