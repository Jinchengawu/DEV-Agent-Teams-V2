from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_team_os.delivery import DeliveryRun, SQLiteDeliveryRepository
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.git import ExternalGitCapabilityProbe, ProjectGitWorkspaces
from agent_team_os.modules.projects import ProjectCatalog, ProjectCreate, SQLiteProjectRepository
from agent_team_os.modules.releases import (
    ExternalForwardReleaseCoordinator,
    ExternalReleaseCatalog,
    ExternalReleaseError,
    GitHubPRReceiptCreate,
    ReleaseBundleV2,
    RemoteApplyReceipt,
    SQLiteExternalReleaseRepository,
    WorkspaceCandidateV2,
    WorkspaceCandidateV2Create,
)
from agent_team_os.modules.workcells import (
    ProjectWorkcellGovernance,
    SQLiteProjectWorkcellRepository,
    SQLiteTeamTemplateRepository,
    TeamTemplateCatalog,
)
from agent_team_os.shared.hashes import sha256_json


class FakeForwardRemote:
    def __init__(self, revisions: dict[str, str], *, fail_once_at: int) -> None:
        self.revisions = revisions
        self.fail_once_at: int | None = fail_once_at

    def revision(self, candidate: WorkspaceCandidateV2) -> str:
        return self.revisions[candidate.repository_uri]

    def apply(
        self,
        candidate: WorkspaceCandidateV2,
        *,
        ordinal: int,
    ) -> RemoteApplyReceipt:
        if self.fail_once_at == ordinal:
            self.fail_once_at = None
            raise ExternalReleaseError("REMOTE_PUSH_REJECTED", "deterministic partial failure")
        assert self.revisions[candidate.repository_uri] == candidate.base_revision
        self.revisions[candidate.repository_uri] = candidate.candidate_revision
        payload = {
            "delivery_id": candidate.delivery_id,
            "ordinal": ordinal,
            "candidate_id": candidate.id,
            "workcell_key": candidate.workcell_key,
            "repository_uri": candidate.repository_uri,
            "before_revision": candidate.base_revision,
            "candidate_revision": candidate.candidate_revision,
            "after_revision": candidate.candidate_revision,
            "recovered": False,
        }
        return RemoteApplyReceipt(**payload, receipt_sha256=sha256_json(payload))


def _bare_repository(root: Path, name: str) -> tuple[Path, str]:
    remote = root / f"{name}.git"
    work = root / f"{name}-seed"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(work)],
        check=True,
        capture_output=True,
    )
    (work / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    environment = {
        "GIT_AUTHOR_NAME": "Agent-Team-OS Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Agent-Team-OS Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    subprocess.run(["git", "add", "README.md"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=work,
        env={**__import__("os").environ, **environment},
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", str(remote), "main"],
        cwd=work,
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return remote, revision


def _release_fixture(
    tmp_path: Path,
) -> tuple[
    SQLiteProjectRepository,
    SQLiteExternalReleaseRepository,
    ExternalReleaseCatalog,
    ReleaseBundleV2,
    dict[str, str],
]:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    project_repository = SQLiteProjectRepository(database)
    teams = TeamTemplateCatalog(SQLiteTeamTemplateRepository(database))
    governance = ProjectWorkcellGovernance(
        SQLiteProjectWorkcellRepository(database),
        teams=teams,
        projects=project_repository,
        managed_git=ProjectGitWorkspaces(tmp_path / "managed"),
        external_git=ExternalGitCapabilityProbe(
            tmp_path / "probe",
            allow_local_test_transport=True,
        ),
    )
    projects = ProjectCatalog(
        project_repository,
        ProjectGitWorkspaces(tmp_path / "managed"),
        team_governance=governance,
    )
    projects.create(
        ProjectCreate(
            id="external-release",
            name="External Release",
            default_pipeline_revision_id="four-workcell:1",
            team_template_revision_id="software-delivery-team:1",
        ),
        "admin",
    )
    roles = ("design", "frontend", "backend", "qa")
    bases: dict[str, str] = {}
    workspace_ids: dict[str, str] = {}
    for role in roles:
        remote, base = _bare_repository(tmp_path, role)
        assignment = governance.create_workspace_binding(
            "external-release",
            {
                "workcell_key": role,
                "kind": "git_repository_v1",
                "adapter_type": "external-git",
                "repository_uri": str(remote),
            },
        )
        governance.verify_workspace(
            assignment.workspace_binding.id,
            expected_version=assignment.workspace_binding.version,
        )
        bases[str(remote)] = base
        workspace_ids[role] = assignment.workspace_binding.id
    governance.activate("external-release", expected_version=1)
    SQLiteDeliveryRepository(database).save(
        DeliveryRun(
            id="delivery-external-release",
            project_id="external-release",
            workspace_id="project:external-release",
            user_request="四仓发布",
            status="applying",
            version=1,
            resolved_journey_sha256="a" * 64,
            evidence_identity="deterministic-test",
            planning_identity="deterministic-test",
        )
    )
    project_repository.acquire_lease("external-release", "delivery-external-release")
    repository = SQLiteExternalReleaseRepository(database)
    catalog = ExternalReleaseCatalog(repository)
    for index, role in enumerate(roles, start=1):
        repository_uri = next(uri for uri in bases if Path(uri).stem == role)
        candidate = catalog.record_candidate(
            WorkspaceCandidateV2Create(
                delivery_id="delivery-external-release",
                project_id="external-release",
                workcell_key=role,
                workspace_binding_id=workspace_ids[role],
                repository_uri=repository_uri,
                adapter_type="external-git",
                base_revision=bases[repository_uri],
                candidate_revision=f"{index}" * 40,
                diff_sha256=f"{index}" * 64,
                verification_sha256=f"{index + 4}" * 64,
                review_artifact_ids=(f"review-{role}",),
            )
        )
        catalog.record_pr(
            candidate.id,
            GitHubPRReceiptCreate(
                pull_request_id=index,
                url=f"https://github.com/example/{role}/pull/{index}",
                head_branch=candidate.candidate_branch,
                head_candidate_sha=candidate.candidate_revision,
                state="open",
            ),
        )
    bundle = catalog.build_bundle(
        delivery_id="delivery-external-release",
        project_id="external-release",
        pipeline_revision_id="four-workcell:1",
        release_contract_snapshot=roles,
    )
    return project_repository, repository, catalog, bundle, bases


def test_partial_apply_never_rolls_back_and_same_bundle_resume_forward_recovers(
    tmp_path: Path,
) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    remote = FakeForwardRemote(dict(bases), fail_once_at=2)
    coordinator = ExternalForwardReleaseCoordinator(repository, remote)

    with pytest.raises(ExternalReleaseError) as failed:
        coordinator.apply(bundle)

    assert failed.value.code == "REMOTE_PUSH_REJECTED"
    attempt = repository.get_attempt(bundle.delivery_id)
    assert attempt is not None and attempt.status == "needs_attention"
    receipts = repository.list_remote_receipts(bundle.delivery_id)
    assert [item.workcell_key for item in receipts] == ["design", "frontend"]
    assert all(
        remote.revision(candidate) == candidate.candidate_revision
        for candidate in bundle.candidates[:2]
    )
    assert all(
        remote.revision(candidate) == candidate.base_revision
        for candidate in bundle.candidates[2:]
    )
    assert coordinator.health(bundle.project_id).status == "release_drifted"
    assert projects.active_delivery_id(bundle.project_id) == bundle.delivery_id
    delivery = SQLiteDeliveryRepository(repository.database).get(bundle.delivery_id)
    assert delivery is not None and delivery.status == "needs_attention"
    assert repository.get_manifest(bundle.project_id) is None
    partial_view = coordinator.details(bundle.delivery_id)
    assert len(partial_view.candidates) == 4
    assert len(partial_view.pull_requests) == 4
    assert partial_view.apply_attempt is not None
    assert partial_view.apply_attempt.status == "needs_attention"
    assert [item.workcell_key for item in partial_view.remote_apply_receipts] == [
        "design",
        "frontend",
    ]
    assert partial_view.manifest is None

    manifest = coordinator.resume_forward(bundle.delivery_id)

    assert [item.workcell_key for item in manifest.repositories] == list(
        bundle.release_contract_snapshot
    )
    assert all(
        remote.revision(candidate) == candidate.candidate_revision
        for candidate in bundle.candidates
    )
    assert coordinator.health(bundle.project_id).status == "healthy"
    assert projects.active_delivery_id(bundle.project_id) is None
    completed = SQLiteDeliveryRepository(repository.database).get(bundle.delivery_id)
    assert completed is not None and completed.status == "completed"
    completed_view = coordinator.details(bundle.delivery_id)
    assert completed_view.manifest == manifest


def test_resume_forward_refuses_drift_without_rewriting_bundle(tmp_path: Path) -> None:
    _projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    remote = FakeForwardRemote(dict(bases), fail_once_at=1)
    coordinator = ExternalForwardReleaseCoordinator(repository, remote)
    with pytest.raises(ExternalReleaseError):
        coordinator.apply(bundle)
    unapplied = bundle.candidates[1]
    remote.revisions[unapplied.repository_uri] = "f" * 40

    with pytest.raises(ExternalReleaseError) as drift:
        coordinator.resume_forward(bundle.delivery_id)

    assert drift.value.code == "RELEASE_UNAPPLIED_REPOSITORY_BASE_DRIFT"
    assert repository.get_manifest(bundle.project_id) is None
