"""使用实际四仓验证产物，验证 Release 不接受孤立或被替换的上游来源。"""

from datetime import UTC, datetime

import pytest
from test_fullstack_verification import verified_four_repositories  # noqa: F401
from test_workcell_execution_kernel import _snapshot

from agent_team_os.modules.releases.acceptance_application import ReleaseAcceptanceVerifierV2
from agent_team_os.modules.workcells.execution_domain import (
    CandidateVerification,
    WorkcellResult,
    WorkcellRun,
    WorkcellRunTree,
)
from agent_team_os.modules.workcells.verification_domain import VerificationPackagePublicationV1
from agent_team_os.shared.hashes import sha256_json


def _verification(run_id, report):
    payload = dict(
        workcell_run_id=run_id,
        writer_agent_run_id=run_id + "-writer",
        candidate_sha=report.candidate_sha,
        diff_sha256=report.diff_sha256,
        status="passed",
        report=report.model_dump(mode="json"),
    )
    return CandidateVerification(**payload, sha256=sha256_json(payload))


def _result(run_id, verification, outputs):
    payload = dict(
        workcell_run_id=run_id,
        candidate_sha=verification.candidate_sha,
        diff_sha256=verification.diff_sha256,
        verification_sha256=verification.sha256,
        review_artifact_ids=(),
        output_artifact_references=[r.model_dump(mode="json") for r in outputs],
        knowledge_citation_ids=(),
    )
    return WorkcellResult(**payload, sha256=sha256_json(payload))


@pytest.fixture
def release_sources(verified_four_repositories, tmp_path):  # noqa: F811
    _root, store, actual = verified_four_repositories
    trees, publications = [], {}
    for role, data in actual.items():
        qualification = data["qualification"]
        inputs = tuple(publications[c] for c in qualification.profile.input_contracts)
        report = data["report"].model_copy(update={"inputs": inputs})
        run_id = "verified-" + role
        verification = _verification(run_id, report)
        outputs = ()
        if report.output_manifest is not None:
            publication = VerificationPackagePublicationV1(
                delivery_id=report.delivery_id,
                workcell_key=role,
                candidate_sha=report.candidate_sha,
                verification_sha256=verification.sha256,
                manifest=report.output_manifest,
            )
            reference = store.put_json(publication.model_dump(mode="json"))
            publications[qualification.profile.output_contract] = reference
            outputs = (reference,)
        snapshot = _snapshot()
        snapshot = snapshot.model_copy(
            update={
                "workcell_key": role,
                "stage_path": role + ".delivery",
                "input_artifacts": inputs,
                "workspace": snapshot.workspace.model_copy(
                    update={"verification_profile": qualification}
                ),
            }
        )
        run = WorkcellRun(
            id=run_id,
            delivery_id=report.delivery_id,
            pipeline_run_id="pipeline",
            stage_attempt_id=role,
            stage_path=snapshot.stage_path,
            loop_iteration=1,
            workcell_key=role,
            workcell_snapshot=snapshot,
            workcell_snapshot_sha256=sha256_json(snapshot.model_dump(mode="json")),
            status="succeeded",
            version=1,
            deadline_at=datetime.now(UTC),
        )
        trees.append(
            WorkcellRunTree(
                workcell_run=run,
                verification=verification,
                result=_result(run_id, verification, outputs),
            )
        )
    verifier = ReleaseAcceptanceVerifierV2(
        database=tmp_path / "release.sqlite",
        project_root=tmp_path,
        artifact_root=store.root,
        remote=None,
        knowledge_guard=None,
    )
    return verifier, tuple(trees), store, actual


def test_release_accepts_actual_four_repository_verification_sources(release_sources):
    verifier, trees, _store, _actual = release_sources
    assert verifier._verify_workcell_results(trees)


@pytest.mark.parametrize(
    "fault",
    [
        "unregistered_source",
        "wrong_verification",
        "unfrozen_input",
        "unconsumed_frozen_publication",
        "duplicate_frozen_publication",
        "wrong_delivery",
        "failed_source",
        "missing_publication",
    ],
)
def test_release_rejects_disconnected_package_even_with_valid_report_hash(release_sources, fault):
    verifier, original, store, actual = release_sources
    trees = list(original)
    design, frontend = trees[:2]
    if fault in {"unregistered_source", "wrong_verification"}:
        # 两个对象都在 Store 且各自内容寻址；区别在于是否属于实际最终成功来源。
        reference = actual["design"]["source"].publication
        report = actual["frontend"]["report"].model_copy(update={"inputs": (reference,)})
        verification = _verification(frontend.workcell_run.id, report)
        publication = VerificationPackagePublicationV1(
            delivery_id=report.delivery_id,
            workcell_key="frontend",
            candidate_sha=report.candidate_sha,
            verification_sha256=verification.sha256,
            manifest=report.output_manifest,
        )
        output = store.put_json(publication.model_dump(mode="json"))
        snapshot = frontend.workcell_run.workcell_snapshot.model_copy(
            update={"input_artifacts": (reference,)}
        )
        trees[1] = frontend.model_copy(
            update={
                "workcell_run": frontend.workcell_run.model_copy(
                    update={"workcell_snapshot": snapshot}
                ),
                "verification": verification,
                "result": _result(frontend.workcell_run.id, verification, (output,)),
            }
        )
        if fault == "wrong_verification":
            trees[0] = design.model_copy(
                update={
                    "result": _result(design.workcell_run.id, design.verification, (reference,))
                }
            )
    elif fault in {
        "unfrozen_input",
        "unconsumed_frozen_publication",
        "duplicate_frozen_publication",
    }:
        inputs = frontend.workcell_run.workcell_snapshot.input_artifacts
        if fault == "unfrozen_input":
            inputs = ()
        else:
            extra = (
                inputs[0]
                if fault == "duplicate_frozen_publication"
                else actual["design"]["source"].publication
            )
            inputs = (*inputs, extra)
        snapshot = frontend.workcell_run.workcell_snapshot.model_copy(
            update={"input_artifacts": inputs}
        )
        trees[1] = frontend.model_copy(
            update={
                "workcell_run": frontend.workcell_run.model_copy(
                    update={"workcell_snapshot": snapshot}
                )
            }
        )
    elif fault == "wrong_delivery":
        trees[0] = design.model_copy(
            update={
                "workcell_run": design.workcell_run.model_copy(
                    update={"delivery_id": "another-delivery"}
                )
            }
        )
    elif fault == "failed_source":
        trees[0] = design.model_copy(
            update={"workcell_run": design.workcell_run.model_copy(update={"status": "failed"})}
        )
    else:
        trees[0] = design.model_copy(
            update={"result": _result(design.workcell_run.id, design.verification, ())}
        )
    assert not verifier._verify_workcell_results(tuple(trees))
