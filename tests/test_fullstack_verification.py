from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_team_os.infrastructure.git import ExternalCandidateEvidence
from agent_team_os.infrastructure.verification.command_toolchain import LocalVerificationToolchain
from agent_team_os.infrastructure.verification.packages import (
    materialize_package,
)
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.workcells.stage_driver import CommandWorkcellMachineVerifier
from agent_team_os.modules.workcells.verification_application import VerificationProfileCatalog
from agent_team_os.modules.workcells.verification_domain import (
    VerificationPackageManifestV1,
    VerificationPackageMember,
    VerificationPackagePublicationV1,
    VerificationReportV2,
    VerificationSourceV2,
)
from agent_team_os.modules.workcells.verification_evidence import validate_report_v2
from agent_team_os.modules.workcells.verification_packages import (
    resolve_publication,
    validate_members,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.verification import VerificationQualificationV2

TEMPLATES = Path(__file__).parents[1] / "examples/health-contract-v1"
PROFILES = {
    "design": "design-contract-v1",
    "frontend": "frontend-ts-vite-vitest-v1",
    "backend": "backend-python-http-v1",
    "qa": "qa-playwright-artifacts-v1",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True, check=True
    ).stdout.strip()


def repository(root: Path, role: str) -> tuple[Path, ExternalCandidateEvidence]:
    directory = root / role
    shutil.copytree(
        TEMPLATES / role,
        directory,
        ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__", "*.pyc"),
    )
    git(directory, "init", "-b", "main")
    git(
        directory,
        "-c",
        "user.name=Local verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Local baseline",
    )
    base = git(directory, "rev-parse", "HEAD")
    git(directory, "add", ".")
    git(
        directory,
        "-c",
        "user.name=Local verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "commit",
        "-m",
        "Isolated health candidate",
    )
    revision = git(directory, "rev-parse", "HEAD")
    diff = subprocess.run(
        ("git", "-C", str(directory), "diff", base, revision), capture_output=True, check=True
    ).stdout
    return directory, ExternalCandidateEvidence(
        base_revision=base,
        candidate_revision=revision,
        diff_sha256=hashlib.sha256(diff).hexdigest(),
        candidate_branch="main",
        changed_files=tuple(git(directory, "diff", "--name-only", base, revision).splitlines()),
    )


@pytest.fixture(scope="module")
def verified_four_repositories(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-four-repositories")
    store = ContentAddressedArtifactStorage(root / "store")
    catalog = VerificationProfileCatalog()
    results = {}
    for role, profile_id in PROFILES.items():
        workspace, candidate = repository(root, role)
        qualification = catalog.qualify(
            profile_id, LocalVerificationToolchain(), workspace=workspace
        )
        assert isinstance(qualification, VerificationQualificationV2)
        sources = tuple(
            value["source"]
            for key, value in results.items()
            if value["source"] is not None
            and value["qualification"].profile.output_contract
            in qualification.profile.input_contracts
        )
        outcome = asyncio.run(
            CommandWorkcellMachineVerifier(store).verify(
                workcell_key=role,
                workspace=workspace,
                candidate=candidate,
                profile=qualification,
                delivery_id="actual-health-four-repositories",
                sources=sources,
            )
        )
        report = VerificationReportV2.model_validate(outcome.report)
        assert outcome.status == "passed", (
            role,
            [store.get_bytes(step.log).decode() for step in report.steps],
        )
        validate_report_v2(report, qualification, store)
        source = None
        if report.output_manifest is not None:
            verification_sha = sha256_json(outcome.report)
            publication = VerificationPackagePublicationV1(
                delivery_id=report.delivery_id,
                workcell_key=role,
                candidate_sha=candidate.candidate_revision,
                verification_sha256=verification_sha,
                manifest=report.output_manifest,
            )
            source = VerificationSourceV2(
                publication=store.put_json(publication.model_dump(mode="json")),
                qualification=qualification,
                report=report,
                verification_sha256=verification_sha,
            )
        results[role] = dict(
            source=source,
            qualification=qualification,
            report=report,
            workspace=workspace,
            candidate=candidate,
        )
        assert not git(workspace, "status", "--porcelain")
    return root, store, results


def test_real_four_repository_design_typescript_http_and_browser(verified_four_repositories):
    _root, _store, results = verified_four_repositories
    assert [step.step for step in results["frontend"]["report"].steps] == [
        "typecheck",
        "test",
        "build",
    ]
    assert results["frontend"]["report"].steps[1].passed >= 1
    assert results["backend"]["report"].steps[1].passed == 4
    assert results["qa"]["report"].steps[0].passed == 4
    assert len(results["qa"]["report"].inputs) == 3


@pytest.mark.parametrize(
    "name", ["../outside", "/tmp/outside", "unknown.json", "src/../../outside"]
)
def test_package_rejects_unknown_or_escaping_members(tmp_path: Path, name: str):
    store = ContentAddressedArtifactStorage(tmp_path / "store")
    member = VerificationPackageMember(path=name, content=store.put_json({}))
    manifest = VerificationPackageManifestV1(
        package_contract="health-design-v1",
        delivery_id="one",
        workcell_key="design",
        candidate_sha="1" * 40,
        profile_sha256="2" * 64,
        qualification_sha256="3" * 64,
        members=(
            *(
                VerificationPackageMember(path=path, content=store.put_json({}))
                for path in ("contract.json", "schema.json", "vectors.json")
            ),
            member,
        ),
    )
    with pytest.raises(ProductError):
        validate_members(manifest)


def test_package_refuses_source_mismatch_and_symlink_destination(
    verified_four_repositories, tmp_path
):
    _root, store, results = verified_four_repositories
    source = results["design"]["source"]
    with pytest.raises(ProductError):
        resolve_publication(
            store,
            source.publication,
            delivery_id="another-delivery",
            source_report=source.report,
            verification_sha256=source.verification_sha256,
        )
    manifest = resolve_publication(
        store,
        source.publication,
        delivery_id=source.report.delivery_id,
        source_report=source.report,
        verification_sha256=source.verification_sha256,
    )
    (tmp_path / "destination").symlink_to(tmp_path / "outside")
    with pytest.raises(ProductError):
        materialize_package(store, manifest, tmp_path / "destination")


def test_frozen_configuration_changes_cannot_run(verified_four_repositories):
    _root, store, results = verified_four_repositories
    frontend = results["frontend"]
    config = frontend["workspace"] / "package.json"
    original = config.read_bytes()
    try:
        config.write_text('{"scripts":{"test:ci":"echo passed"}}')
        with pytest.raises(ProductError):
            asyncio.run(
                CommandWorkcellMachineVerifier(store).verify(
                    workcell_key="frontend",
                    workspace=frontend["workspace"],
                    candidate=frontend["candidate"],
                    profile=frontend["qualification"],
                    delivery_id="actual-health-four-repositories",
                )
            )
    finally:
        config.write_bytes(original)


@pytest.mark.parametrize(
    "test_source",
    [
        "console.log(JSON.stringify({numTotalTests: 1, numPassedTests: 1, success: true}));\n",
        "import { test } from 'vitest'; test.skip('pretend', () => {});\n",
    ],
)
def test_frontend_refuses_zero_or_skipped_tests_despite_printed_success(
    verified_four_repositories, test_source
):
    _root, store, results = verified_four_repositories
    frontend = results["frontend"]
    workspace = frontend["workspace"]
    original_revision = git(workspace, "rev-parse", "HEAD")
    try:
        for path in (workspace / "tests").glob("*.test.ts"):
            path.unlink()
        (workspace / "tests/pretend.test.ts").write_text(test_source)
        git(workspace, "add", "tests")
        git(
            workspace,
            "-c",
            "user.name=Verifier",
            "-c",
            "user.email=verifier@example.invalid",
            "commit",
            "-m",
            "Negative test candidate",
        )
        candidate = frontend["candidate"].model_copy(
            update={"candidate_revision": git(workspace, "rev-parse", "HEAD")}
        )
        outcome = asyncio.run(
            CommandWorkcellMachineVerifier(store).verify(
                workcell_key="frontend",
                workspace=workspace,
                candidate=candidate,
                profile=frontend["qualification"],
                delivery_id="actual-health-four-repositories",
                sources=(results["design"]["source"],),
            )
        )
        report = VerificationReportV2.model_validate(outcome.report)
        assert outcome.status == "failed"
        assert report.steps[-1].step == "test"
        assert not report.steps[-1].result_contract_passed
        assert report.output_manifest is None
    finally:
        git(workspace, "reset", "--hard", original_revision)


def test_package_budget_checked_before_reading_oversized_file(tmp_path):
    from agent_team_os.infrastructure.verification.packages import create_package

    store = ContentAddressedArtifactStorage(tmp_path / "store")
    build = tmp_path / "build"
    build.mkdir()
    with (build / "index.html").open("wb") as stream:
        stream.truncate(20_000_001)
    qualification = VerificationProfileCatalog().qualify(
        "frontend-ts-vite-vitest-v1",
        LocalVerificationToolchain(),
        workspace=TEMPLATES / "frontend",
    )
    with pytest.raises(ProductError):
        create_package(
            store,
            root=build,
            qualification=qualification,
            delivery_id="oversize",
            candidate_sha="1" * 40,
        )
    assert not any(path.is_file() for path in store.root.rglob("*"))


def test_self_consistent_package_forgery_cannot_change_verified_output(verified_four_repositories):
    _root, store, results = verified_four_repositories
    source = results["design"]["source"]
    original = VerificationPackagePublicationV1.model_validate(store.get_json(source.publication))
    manifest = VerificationPackageManifestV1.model_validate(store.get_json(original.manifest))
    forged = manifest.model_copy(update={"candidate_sha": "f" * 40})
    forged_publication = original.model_copy(
        update={"manifest": store.put_json(forged.model_dump(mode="json"))}
    )
    reference = store.put_json(forged_publication.model_dump(mode="json"))
    with pytest.raises(ProductError):
        resolve_publication(
            store,
            reference,
            delivery_id=source.report.delivery_id,
            source_report=source.report,
            verification_sha256=source.verification_sha256,
        )


def test_verification_report_rejects_duplicate_inputs_and_fake_counts(verified_four_repositories):
    _root, store, results = verified_four_repositories
    qa = results["qa"]
    report = qa["report"]
    duplicated = report.model_copy(update={"inputs": (report.inputs[0],) * 3})
    with pytest.raises(ProductError):
        validate_report_v2(duplicated, qa["qualification"], store)
    step = report.steps[0].model_copy(update={"passed": 99, "discovered": 99})
    with pytest.raises(ProductError):
        validate_report_v2(report.model_copy(update={"steps": (step,)}), qa["qualification"], store)
