from __future__ import annotations

import sqlite3
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
    ReleaseApplyAttemptV2,
    ReleaseBundleV2,
    ReleaseManifestV2,
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
        remote.revision(candidate) == candidate.base_revision for candidate in bundle.candidates[2:]
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


def test_finalization_failure_keeps_recovery_lease_and_resumes_without_reapplying(
    tmp_path: Path,
) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    remote = FakeForwardRemote(dict(bases), fail_once_at=-1)
    coordinator = ExternalForwardReleaseCoordinator(repository, remote)
    # 在最后的完成事件写入处注入真实数据库失败，覆盖整个最终提交的原子性。
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            """CREATE TRIGGER fail_release_completion BEFORE INSERT ON product_events
            WHEN NEW.event_type='delivery.completed'
            BEGIN SELECT RAISE(ABORT, 'finalization persistence failed'); END"""
        )

    with pytest.raises(ExternalReleaseError):
        coordinator.apply(bundle)

    assert all(
        remote.revision(candidate) == candidate.candidate_revision
        for candidate in bundle.candidates
    )
    assert len(repository.list_remote_receipts(bundle.delivery_id)) == 4
    attempt = repository.get_attempt(bundle.delivery_id)
    assert attempt is not None and attempt.status == "needs_attention"
    assert repository.get_manifest(bundle.project_id) is None
    assert coordinator.health(bundle.project_id).status == "release_drifted"
    assert projects.active_delivery_id(bundle.project_id) == bundle.delivery_id
    deliveries = SQLiteDeliveryRepository(repository.database)
    delivery = deliveries.get(bundle.delivery_id)
    assert delivery is not None and delivery.status == "needs_attention"
    assert not any(
        event.event_type == "delivery.completed"
        for event in deliveries.list_events(bundle.delivery_id)
    )

    with sqlite3.connect(repository.database) as connection:
        connection.execute("DROP TRIGGER fail_release_completion")
    manifest = coordinator.resume_forward(bundle.delivery_id)

    assert repository.get_manifest(bundle.project_id) == manifest
    completed = repository.get_attempt(bundle.delivery_id)
    assert completed is not None and completed.status == "completed"
    assert coordinator.health(bundle.project_id).status == "healthy"
    assert projects.active_delivery_id(bundle.project_id) is None
    delivery = deliveries.get(bundle.delivery_id)
    assert delivery is not None and delivery.status == "completed"
    assert delivery.release_manifest_v2_sha256 == manifest.manifest_sha256
    assert (
        sum(
            event.event_type == "delivery.completed"
            for event in deliveries.list_events(bundle.delivery_id)
        )
        == 1
    )


def test_committed_finalization_with_lost_acknowledgement_stays_completed(
    tmp_path: Path,
) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)

    class LostAcknowledgementRepository(SQLiteExternalReleaseRepository):
        def finalize_release(
            self,
            bundle: ReleaseBundleV2,
            attempt: ReleaseApplyAttemptV2,
            manifest: ReleaseManifestV2,
        ) -> None:
            super().finalize_release(bundle, attempt, manifest)
            raise OSError("completion acknowledgement lost after commit")

    coordinator = ExternalForwardReleaseCoordinator(
        LostAcknowledgementRepository(repository.database),
        FakeForwardRemote(dict(bases), fail_once_at=-1),
    )

    manifest = coordinator.apply(bundle)

    assert coordinator.apply(bundle) == manifest
    assert coordinator.resume_forward(bundle.delivery_id) == manifest
    assert repository.get_manifest(bundle.project_id) == manifest
    assert coordinator.health(bundle.project_id).status == "healthy"
    assert projects.active_delivery_id(bundle.project_id) is None
    attempt = repository.get_attempt(bundle.delivery_id)
    assert attempt is not None and attempt.status == "completed"
    deliveries = SQLiteDeliveryRepository(repository.database)
    delivery = deliveries.get(bundle.delivery_id)
    assert delivery is not None and delivery.status == "completed"
    assert not any(
        event.event_type == "delivery.needs_attention"
        for event in deliveries.list_events(bundle.delivery_id)
    )


def test_recovery_owners_survive_legacy_terminal_delivery_and_missing_lease(
    tmp_path: Path,
) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    assert repository.project_recovery_delivery_ids(bundle.project_id) == ()
    coordinator = ExternalForwardReleaseCoordinator(
        repository, FakeForwardRemote(dict(bases), fail_once_at=0)
    )
    with pytest.raises(ExternalReleaseError):
        coordinator.apply(bundle)
    deliveries = SQLiteDeliveryRepository(repository.database)
    delivery = deliveries.get(bundle.delivery_id)
    assert delivery is not None
    deliveries.save(delivery.model_copy(update={"status": "cancelled"}))
    projects.release_lease(bundle.project_id, bundle.delivery_id)

    assert repository.project_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)
    assert repository.project_recovery_delivery_ids("another-project") == ()
    # 历史不一致时，即使 Attempt 被误记为完成，drifted Health 仍保留恢复所有者。
    attempt = repository.get_attempt(bundle.delivery_id)
    assert attempt is not None
    repository.put_attempt(
        attempt.model_copy(update={"status": "completed", "version": attempt.version + 1}),
        expected_version=attempt.version,
    )
    assert repository.project_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)
    health = coordinator.health(bundle.project_id)
    repository.put_health(health.model_copy(update={"status": "healthy"}))
    assert repository.project_recovery_delivery_ids(bundle.project_id) == ()


def test_resume_repairs_a_legacy_completed_delivery_projection(tmp_path: Path) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)
    coordinator = ExternalForwardReleaseCoordinator(
        repository, FakeForwardRemote(dict(bases), fail_once_at=2)
    )
    with pytest.raises(ExternalReleaseError):
        coordinator.apply(bundle)
    deliveries = SQLiteDeliveryRepository(repository.database)
    delivery = deliveries.get(bundle.delivery_id)
    assert delivery is not None
    deliveries.save(delivery.model_copy(update={"status": "completed", "error_code": None}))
    assert repository.project_recovery_delivery_ids(bundle.project_id) == (bundle.delivery_id,)

    manifest = coordinator.resume_forward(bundle.delivery_id)

    repaired = deliveries.get(bundle.delivery_id)
    assert repaired is not None and repaired.release_manifest_v2_sha256 == manifest.manifest_sha256
    assert projects.active_delivery_id(bundle.project_id) is None
    assert repository.project_recovery_delivery_ids(bundle.project_id) == ()


def test_finalization_failure_uses_latest_attempt_version_for_recovery(tmp_path: Path) -> None:
    projects, repository, _catalog, bundle, bases = _release_fixture(tmp_path)

    class AdvancedAttemptRepository(SQLiteExternalReleaseRepository):
        fail_once = True

        def finalize_release(
            self,
            bundle: ReleaseBundleV2,
            attempt: ReleaseApplyAttemptV2,
            manifest: ReleaseManifestV2,
        ) -> None:
            if self.fail_once:
                self.fail_once = False
                self.put_attempt(
                    attempt.model_copy(update={"version": attempt.version + 1}),
                    expected_version=attempt.version,
                )
                raise OSError("finalization interrupted after attempt version advanced")
            super().finalize_release(bundle, attempt, manifest)

    coordinator = ExternalForwardReleaseCoordinator(
        AdvancedAttemptRepository(repository.database),
        FakeForwardRemote(dict(bases), fail_once_at=-1),
    )
    with pytest.raises(ExternalReleaseError):
        coordinator.apply(bundle)
    attempt = repository.get_attempt(bundle.delivery_id)
    assert attempt is not None and attempt.status == "needs_attention" and attempt.version == 3
    assert projects.active_delivery_id(bundle.project_id) == bundle.delivery_id

    manifest = coordinator.resume_forward(bundle.delivery_id)

    assert repository.get_finalized_manifest(bundle) == manifest
    assert repository.project_recovery_delivery_ids(bundle.project_id) == ()
