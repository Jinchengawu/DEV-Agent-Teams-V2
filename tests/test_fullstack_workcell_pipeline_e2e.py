"""确定性模型驱动同一 Delivery；真实四仓工具通过 Stage/Publication/Release 全链。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from test_fullstack_verification import PROFILES, TEMPLATES
from test_workcell_pipeline_e2e import (
    FourRepositoryAgent,
    _git,
    _run,
    _run_four_repository_pipeline,
)

from agent_team_os.delivery import DeliveryRun
from agent_team_os.infrastructure.verification.command_toolchain import LocalVerificationToolchain
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.releases.acceptance_domain import ReleaseAcceptanceReportV2
from agent_team_os.modules.workcells import (
    WorkcellAgentInvocation,
    WorkcellAgentOutput,
    WorkcellExecutionModule,
)
from agent_team_os.modules.workcells.verification_application import VerificationProfileCatalog
from agent_team_os.modules.workcells.verification_domain import (
    VerificationPackagePublicationV1,
    VerificationReportV2,
)
from agent_team_os.modules.workcells.verification_evidence import validate_report_v2
from agent_team_os.modules.workcells.verification_packages import resolve_publication
from agent_team_os.shared.verification import VerificationQualificationV2, VerificationSnapshot


class ActualStackWriter(FourRepositoryAgent):
    """只替代模型回复和代码生成，产品验证、Git、产物包及 Release 均调用真实实现。"""

    async def run(self, invocation: WorkcellAgentInvocation) -> WorkcellAgentOutput:
        if invocation.workspace_access != "workspace_write":
            return await super().run(invocation)
        self.invocations.append(invocation)
        changed = []
        root = TEMPLATES / invocation.workcell_key
        for source in sorted(root.rglob("*")):
            relative = source.relative_to(root)
            if (
                not source.is_file()
                or relative.parts[0] not in {"design", "src", "tests"}
                or "__pycache__" in relative.parts
                or source.suffix == ".pyc"
            ):
                continue
            destination = invocation.workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            changed.append(relative.as_posix())
        assert changed
        return WorkcellAgentOutput(
            runtime_identity=self.runtime_identity,
            content={"changed_files": changed},
        )


class ActualFourStackScenario:
    def __init__(self) -> None:
        # 名称对应现有确定性 Runtime 探针；此测试从不调用 Hermes/Codex 进程。
        self.agent = ActualStackWriter("codex-cli:acceptance-test")
        self.profiles: dict[str, VerificationSnapshot] = {}

    def remote(self, root: Path, role: str) -> tuple[Path, str]:
        remote, seed = root / f"{role}.git", root / f"{role}-seed"
        _run("git", "init", "--bare", "--initial-branch=main", str(remote))
        _run("git", "init", "--initial-branch=main", str(seed))
        profile = VerificationProfileCatalog().get(PROFILES[role])
        for name in (*profile.config_paths, "README.md"):
            target = seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((TEMPLATES / role / name).read_bytes())
        _run("git", "add", ".", cwd=seed)
        _run(
            "git",
            "-c",
            "user.name=Verifier",
            "-c",
            "user.email=verifier@example.invalid",
            "commit",
            "-m",
            "Frozen configuration only",
            cwd=seed,
        )
        _run("git", "remote", "add", "origin", str(remote), cwd=seed)
        _run("git", "push", "origin", "main", cwd=seed)
        self.profiles[role] = VerificationProfileCatalog().qualify(
            PROFILES[role], LocalVerificationToolchain(), workspace=seed
        )
        return remote, _git(remote, "rev-parse", "refs/heads/main")

    def assert_completed(
        self,
        delivery: DeliveryRun,
        kernel: WorkcellExecutionModule,
        artifacts: ContentAddressedArtifactStorage,
        acceptance: ReleaseAcceptanceReportV2,
    ) -> None:
        assert delivery.evidence_identity == "deterministic-test"
        assert acceptance.fail == acceptance.warn == acceptance.skipped == 0
        trees = kernel.list_delivery(delivery.id)
        assert len(trees) == 5
        preparation = next(
            tree for tree in trees if "qa-preparation" in tree.workcell_run.stage_path
        )
        assert preparation.workcell_run.status == "succeeded"
        assert preparation.verification is None
        assert preparation.result_validation is not None
        assert preparation.result.candidate_sha is None
        assert all(
            item.workspace_access == "artifact_only"
            for item in preparation.agent_runs
            if item.run_role == "child"
        )

        publications = {}
        reports = {}
        for tree in trees:
            if tree.verification is None:
                continue
            report = VerificationReportV2.model_validate(tree.verification.report)
            qualification = tree.workcell_run.workcell_snapshot.workspace.verification_profile
            assert isinstance(qualification, VerificationQualificationV2)
            assert qualification == self.profiles[report.workcell_key]
            assert report.delivery_id == delivery.id
            assert report.cleanup_completed
            assert not Path(report.execution_root).exists()
            assert tree.result.verification_sha256 == tree.verification.sha256
            assert tree.result.candidate_sha == report.candidate_sha
            validate_report_v2(report, qualification, artifacts)
            reports[report.workcell_key] = (tree, report)
            for reference in tree.result.output_artifact_references:
                if reference.media_type != "application/json":
                    continue
                payload = artifacts.get_json(reference)
                if payload.get("contract_version") != "verification-publication-v1":
                    continue
                publication = VerificationPackagePublicationV1.model_validate(payload)
                assert publication.verification_sha256 == tree.verification.sha256
                manifest = resolve_publication(
                    artifacts,
                    reference,
                    delivery_id=delivery.id,
                    source_report=report,
                    verification_sha256=tree.verification.sha256,
                )
                publications[manifest.package_contract] = reference
        assert len(publications) == 3
        assert set(reports) == set(PROFILES)
        for role, (tree, report) in reports.items():
            expected = {
                publications[contract].sha256
                for contract in self.profiles[role].profile.input_contracts
            }
            assert {reference.sha256 for reference in report.inputs} == expected
            assert all(
                reference in tree.workcell_run.workcell_snapshot.input_artifacts
                for reference in report.inputs
            )
        assert [step.step for step in reports["frontend"][1].steps] == [
            "typecheck",
            "test",
            "build",
        ]
        assert reports["frontend"][1].steps[1].passed == 10
        assert reports["backend"][1].steps[0].passed == 4
        assert reports["backend"][1].steps[1].passed == 4
        assert reports["qa"][1].steps[0].passed == 4


def test_actual_v2_four_stack_stage_publications_and_release_in_one_delivery(
    tmp_path: Path,
) -> None:
    asyncio.run(_run_four_repository_pipeline(tmp_path, scenario=ActualFourStackScenario()))
