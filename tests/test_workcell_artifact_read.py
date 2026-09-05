from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from test_workcell_execution_kernel import _kernel, _snapshot

from agent_team_os.modules.agents import ArtifactEnvelope
from agent_team_os.modules.workcells import (
    DelegationAssignment,
    WorkcellRunCreate,
    create_workcell_execution_router,
)


def test_workcell_artifact_http_requires_delivery_permission_and_registered_output(tmp_path: Path):
    kernel, store = _kernel(tmp_path)
    private_input = store.put_json({"knowledge": "input only"})
    run = kernel.create(WorkcellRunCreate(
        delivery_id="delivery-workcell", pipeline_run_id="p", stage_attempt_id="s",
        snapshot=_snapshot().model_copy(update={"input_artifacts": (private_input,)}),
    ))
    tree = kernel.submit_delegation_plan(run.workcell_run.id, (
        DelegationAssignment(slot_key="delegate_1", delegate_purpose="workspace_write",
                             workspace_access="workspace_write"),
    ))
    child = next(item for item in tree.agent_runs if item.run_role == "child")
    kernel.start_child(child.id)
    artifact = store.put_json({"diff_content": "-old\n+new"})
    kernel.finish_child(child.id, status="failed", artifacts=(ArtifactEnvelope(
        contract_id="workspace-candidate-diff-v1", reference=artifact, sha256=artifact.sha256,
    ),))

    def authorize(request: Request, delivery_id: str):
        if request.headers.get("x-test-reader") != "yes":
            raise HTTPException(status_code=403, detail="not allowed")

    app = FastAPI()
    app.include_router(create_workcell_execution_router(kernel, authorize_read=authorize))
    with TestClient(app) as client:
        prefix = f"/v1/deliveries/delivery-workcell/workcell-runs/{run.workcell_run.id}/artifacts/"
        assert client.get(prefix + artifact.sha256).status_code == 403
        unknown = prefix.replace(run.workcell_run.id, "unknown") + artifact.sha256
        assert client.get(unknown).status_code == 403
        headers = {"x-test-reader": "yes"}
        result = client.get(prefix + artifact.sha256, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["content"] == '{"diff_content":"-old\\n+new"}'
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["x-content-type-options"] == "nosniff"
        assert client.get(prefix + private_input.sha256, headers=headers).status_code == 404
        assert client.get(prefix + "a" * 64, headers=headers).status_code == 404
        other_delivery = prefix.replace("/deliveries/delivery-workcell/", "/deliveries/other/")
        assert client.get(other_delivery + artifact.sha256, headers=headers).status_code == 404
        store.path_for(artifact).write_text("corrupted")
        corrupted = client.get(prefix + artifact.sha256, headers=headers)
        assert corrupted.status_code == 409
        assert isinstance(corrupted.json()["detail"], str)
        assert corrupted.json()["code"] == "ARTIFACT_CONTENT_HASH_MISMATCH"


@pytest.mark.parametrize("failure", ["declared_size", "actual_size", "binary", "invalid_json"])
def test_workcell_artifact_preview_rejects_oversize_and_non_text(tmp_path: Path, failure: str):
    kernel, store = _kernel(tmp_path)
    run = kernel.create(WorkcellRunCreate(
        delivery_id="delivery-workcell", pipeline_run_id="p", stage_attempt_id="s",
        snapshot=_snapshot(),
    ))
    tree = kernel.submit_delegation_plan(run.workcell_run.id, (
        DelegationAssignment(slot_key="delegate_1", delegate_purpose="workspace_write",
                             workspace_access="workspace_write"),
    ))
    child = next(item for item in tree.agent_runs if item.run_role == "child")
    kernel.start_child(child.id)
    payload = b"x" * (1024 * 1024 + 1) if failure == "declared_size" else b"not-json"
    media = "application/octet-stream" if failure == "binary" else "application/json"
    reference = store.put_bytes(payload, media_type=media)
    kernel.finish_child(child.id, status="failed", artifacts=(ArtifactEnvelope(
        contract_id="raw-output", reference=reference, sha256=reference.sha256,
    ),))
    if failure == "actual_size":
        store.path_for(reference).write_bytes(b"x" * (1024 * 1024 + 1))
    app = FastAPI()
    app.include_router(create_workcell_execution_router(kernel))
    with TestClient(app) as client:
        result = client.get(
            f"/v1/deliveries/delivery-workcell/workcell-runs/{run.workcell_run.id}"
            f"/artifacts/{reference.sha256}"
        )
    assert result.status_code == (415 if failure == "binary" else 409)
