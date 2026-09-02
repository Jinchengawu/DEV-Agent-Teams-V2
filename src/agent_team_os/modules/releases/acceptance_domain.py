from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256, sha256_json


class ReleaseAcceptanceCheckV2(BaseModel):
    """One privacy-minimal assertion in a V2 Live Release report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,119}$")
    status: Literal["passed", "failed"]
    detail: str = Field(min_length=1, max_length=500)
    evidence_sha256: Sha256


class ReleaseAcceptanceReportV2(BaseModel):
    """Content-addressed evidence for one already-completed four-repository Delivery."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["release-acceptance-report-v2"] = (
        "release-acceptance-report-v2"
    )
    capability: Literal["feishu-knowledge-delivery-v1"] = (
        "feishu-knowledge-delivery-v1"
    )
    kind: Literal["live"] = "live"
    project_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    delivery_id: str = Field(min_length=1, max_length=200)
    status: Literal["passed", "failed"]
    fail: int = Field(ge=0)
    warn: Literal[0] = 0
    skipped: Literal[0] = 0
    product_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$"
    )
    acwm_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    pipeline_revision_id: str | None = None
    build_identity_sha256: Sha256 | None = None
    knowledge_context_set_sha256: Sha256 | None = None
    workcell_evidence_sha256: Sha256 | None = None
    release_bundle_sha256: Sha256 | None = None
    release_manifest_sha256: Sha256 | None = None
    checks: tuple[ReleaseAcceptanceCheckV2, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_sha256: Sha256

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        delivery_id: str,
        checks: tuple[ReleaseAcceptanceCheckV2, ...],
        product_revision: str | None = None,
        acwm_revision: str | None = None,
        pipeline_revision_id: str | None = None,
        build_identity_sha256: Sha256 | None = None,
        knowledge_context_set_sha256: Sha256 | None = None,
        workcell_evidence_sha256: Sha256 | None = None,
        release_bundle_sha256: Sha256 | None = None,
        release_manifest_sha256: Sha256 | None = None,
        created_at: datetime | None = None,
    ) -> Self:
        failed = sum(check.status == "failed" for check in checks)
        status: Literal["passed", "failed"] = "passed" if failed == 0 else "failed"
        created_at_value = created_at or datetime.now(UTC)
        payload = {
            "schema_version": "release-acceptance-report-v2",
            "capability": "feishu-knowledge-delivery-v1",
            "kind": "live",
            "project_id": project_id,
            "delivery_id": delivery_id,
            "status": status,
            "fail": failed,
            "warn": 0,
            "skipped": 0,
            "product_revision": product_revision,
            "acwm_revision": acwm_revision,
            "pipeline_revision_id": pipeline_revision_id,
            "build_identity_sha256": build_identity_sha256,
            "knowledge_context_set_sha256": knowledge_context_set_sha256,
            "workcell_evidence_sha256": workcell_evidence_sha256,
            "release_bundle_sha256": release_bundle_sha256,
            "release_manifest_sha256": release_manifest_sha256,
            "checks": [check.model_dump(mode="json") for check in checks],
            "created_at": created_at_value,
        }
        return cls(
            schema_version="release-acceptance-report-v2",
            capability="feishu-knowledge-delivery-v1",
            kind="live",
            project_id=project_id,
            delivery_id=delivery_id,
            status=status,
            fail=failed,
            warn=0,
            skipped=0,
            product_revision=product_revision,
            acwm_revision=acwm_revision,
            pipeline_revision_id=pipeline_revision_id,
            build_identity_sha256=build_identity_sha256,
            knowledge_context_set_sha256=knowledge_context_set_sha256,
            workcell_evidence_sha256=workcell_evidence_sha256,
            release_bundle_sha256=release_bundle_sha256,
            release_manifest_sha256=release_manifest_sha256,
            checks=checks,
            created_at=created_at_value,
            report_sha256=sha256_json(payload),
        )

    @model_validator(mode="after")
    def content_hash_and_counters_are_coherent(self) -> Self:
        payload = self.model_dump(mode="python", exclude={"report_sha256"})
        if sha256_json(payload) != self.report_sha256:
            raise ValueError("Release Acceptance Report content hash mismatch")
        failed = sum(check.status == "failed" for check in self.checks)
        if self.fail != failed or self.status != ("passed" if failed == 0 else "failed"):
            raise ValueError("Release Acceptance Report counters are inconsistent")
        if len({check.code for check in self.checks}) != len(self.checks):
            raise ValueError("Release Acceptance Report check codes must be unique")
        return self
