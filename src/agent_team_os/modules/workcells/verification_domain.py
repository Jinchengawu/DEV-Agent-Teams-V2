"""产品验证报告与跨 Workcell 产物包合同。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256
from ...shared.verification import VerificationQualificationV2
from ..artifacts import ArtifactReference


class VerificationStepResultV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: str
    command: tuple[str, ...]
    exit_code: int | None
    status: Literal["passed", "failed", "timed_out"]
    discovered: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    case_ids: tuple[str, ...] = ()
    result_contract_passed: bool
    result: ArtifactReference | None = None
    log: ArtifactReference


class VerificationReportV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["verification-report-v2"] = "verification-report-v2"
    delivery_id: str
    workcell_key: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    diff_sha256: Sha256
    profile_sha256: Sha256
    qualification_sha256: Sha256
    execution_root: str
    steps: tuple[VerificationStepResultV2, ...]
    inputs: tuple[ArtifactReference, ...] = ()
    output_manifest: ArtifactReference | None = None
    cleanup_completed: bool


class VerificationPackageMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    content: ArtifactReference


class VerificationPackageManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["verification-package-v1"] = "verification-package-v1"
    package_contract: str
    delivery_id: str
    workcell_key: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    profile_sha256: Sha256
    qualification_sha256: Sha256
    members: tuple[VerificationPackageMember, ...]


class VerificationPackagePublicationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["verification-publication-v1"] = "verification-publication-v1"
    delivery_id: str
    workcell_key: str
    candidate_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    verification_sha256: Sha256
    manifest: ArtifactReference


class VerificationSourceV2(BaseModel):
    """由产品已持久化 Workcell Result/Verification 派生，不接收外部请求。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    publication: ArtifactReference
    report: VerificationReportV2
    qualification: VerificationQualificationV2
    verification_sha256: Sha256
