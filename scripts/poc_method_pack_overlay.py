from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    MethodPackInstall,
    MethodPackSnapshot,
)


def main() -> int:
    project_root = Path(__file__).parents[1].resolve()
    configuration = json.loads(
        (project_root / "config" / "method-packs-v050.json").read_text(encoding="utf-8")
    )
    git_before = _git_status(project_root)
    with tempfile.TemporaryDirectory(prefix="agent-team-os-method-pack-poc-") as temporary:
        store = ContentAddressedMethodPackStore(Path(temporary) / "store")
        snapshots = tuple(
            _install_package(store, raw) for raw in configuration["packages"]
        )
        with store.runtime_overlay(snapshots) as overlay:
            environment = os.environ.copy()
            environment.update(overlay.environment)
            command = [
                "codex",
                "debug",
                "prompt-input",
                "$bmad-build inspect the installed Method Entry only",
            ]
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "CODEX_METHOD_DISCOVERY_FAILED: " + completed.stderr.strip()
                )
            expected_entry = str(
                overlay.codex_home / "skills" / "bmad-build" / "SKILL.md"
            )
            if expected_entry not in completed.stdout:
                raise RuntimeError("CODEX_METHOD_ENTRY_MISSING")
            if str(overlay.codex_home / "skills" / "bmad-party-mode") in completed.stdout:
                raise RuntimeError("BMAD_PARTY_MODE_EXPOSED")
            codex_version = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout.strip()
    git_after = _git_status(project_root)
    if git_before != git_after:
        raise RuntimeError("METHOD_OVERLAY_POLLUTED_BUSINESS_REPOSITORY")
    print(
        json.dumps(
            {
                "status": "passed",
                "codex_cli": codex_version,
                "git_workspace_unchanged": True,
                "party_mode_exposed": False,
                "packages": [
                    {
                        "package_name": item.package_name,
                        "package_version": item.package_version,
                        "archive_sha256": item.archive_sha256,
                        "content_sha256": item.content_sha256,
                        "qualification_sha256": item.qualification_sha256,
                        "method_entries": [
                            entry.method_id for entry in item.method_entries
                        ],
                    }
                    for item in snapshots
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _install_package(
    store: ContentAddressedMethodPackStore,
    raw: dict[str, object],
) -> MethodPackSnapshot:
    request = MethodPackInstall.model_validate(raw["install"])
    with urllib.request.urlopen(request.tarball_uri, timeout=60) as response:  # noqa: S310
        archive = response.read(request.max_unpacked_bytes + 1)
    snapshot = store.install_archive(request, archive)
    if snapshot.content_sha256 != raw["expected_content_sha256"]:
        raise RuntimeError("METHOD_PACK_CONTENT_HASH_DRIFT")
    if snapshot.qualification_sha256 != raw["expected_qualification_sha256"]:
        raise RuntimeError("METHOD_PACK_QUALIFICATION_HASH_DRIFT")
    return snapshot


def _git_status(project_root: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout


if __name__ == "__main__":
    raise SystemExit(main())
