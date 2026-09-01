from __future__ import annotations

import subprocess
from pathlib import Path

from agent_team_os.infrastructure.git import (
    ExternalForwardGitRemote,
    ExternalGitBinding,
    ExternalGitCapabilityProbe,
    ExternalGitWorkspaceManager,
    ExternalWriterPolicy,
)
from agent_team_os.modules.releases import WorkspaceCandidateV2
from agent_team_os.shared.hashes import sha256_json


def test_external_git_capability_probe_checks_existing_main_without_mutating_remote(
    tmp_path: Path,
) -> None:
    remote = _seed_bare_repository(tmp_path)
    before = _refs(remote)

    receipt = ExternalGitCapabilityProbe(
        tmp_path / "probe",
        allow_local_test_transport=True,
    ).verify(
        ExternalGitBinding(
            remote_uri=str(remote),
            default_branch="main",
            credential_reference=None,
        )
    )

    assert receipt.status == "ready"
    assert receipt.remote_main_sha == before["refs/heads/main"]
    assert receipt.direct_fast_forward_main is True
    assert receipt.transport == "local-test"
    assert _refs(remote) == before


def test_external_writer_candidate_review_view_and_forward_only_apply(
    tmp_path: Path,
) -> None:
    remote = _seed_bare_repository(tmp_path)
    base = _refs(remote)["refs/heads/main"]
    binding = ExternalGitBinding(remote_uri=str(remote))
    manager = ExternalGitWorkspaceManager(tmp_path / "workspace-manager")
    writer = manager.prepare_writer(
        workspace_binding_id="workspace-frontend",
        delivery_id="delivery-123",
        workcell_key="frontend",
        binding=binding,
        expected_base_revision=base,
    )
    source = writer.worktree / "src" / "app.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const ready = true;\n", encoding="utf-8")

    evidence = manager.freeze_candidate(
        writer,
        policy=ExternalWriterPolicy(allowed_paths=("src/**",)),
    )

    refs = _refs(remote)
    assert refs["refs/heads/main"] == base
    assert refs[f"refs/heads/{evidence.candidate_branch}"] == evidence.candidate_revision
    review = manager.prepare_review_view(
        writer,
        candidate_revision=evidence.candidate_revision,
    )
    assert (review / "src" / "app.ts").read_text(encoding="utf-8").endswith("true;\n")
    assert (review / "src" / "app.ts").stat().st_mode & 0o222 == 0

    candidate_payload = {
        "delivery_id": "delivery-123",
        "project_id": "external-project",
        "workcell_key": "frontend",
        "workspace_binding_id": "workspace-frontend",
        "repository_uri": str(remote),
        "adapter_type": "external-git",
        "base_revision": base,
        "candidate_revision": evidence.candidate_revision,
        "diff_sha256": evidence.diff_sha256,
        "candidate_branch": evidence.candidate_branch,
        "verification_sha256": "a" * 64,
        "review_artifact_ids": ("review-1",),
        "status": "verified",
    }
    candidate = WorkspaceCandidateV2(
        **candidate_payload,
        evidence_sha256=sha256_json(candidate_payload),
    )
    applier = ExternalForwardGitRemote(lambda workspace_id: binding)
    receipt = applier.apply(candidate, ordinal=0)

    assert receipt.after_revision == evidence.candidate_revision
    assert receipt.recovered is False
    assert _refs(remote)["refs/heads/main"] == evidence.candidate_revision


def _seed_bare_repository(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    _git(tmp_path, "init", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("# Existing private repository\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(
        seed,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "seed",
    )
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")
    return remote


def _refs(remote: Path) -> dict[str, str]:
    output = _git(remote, "show-ref")
    return {
        reference: revision
        for revision, reference in (line.split(" ", 1) for line in output.splitlines())
    }


def _git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
