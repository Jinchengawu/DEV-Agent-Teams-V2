from __future__ import annotations

from typing import Final, Literal

from acwm.domain import ArtifactContract, ArtifactModality

from .shared.hashes import Sha256, sha256_json

KNOWLEDGE_CONTEXT_CONTRACT_ID: Final[Literal["knowledge-context-v1"]] = (
    "knowledge-context-v1"
)
KNOWLEDGE_CONTEXT_CONTRACT_VERSION = "1.0.0"
KNOWLEDGE_CONTEXT_STAGE_PATHS = (
    "requirements",
    "tasking",
    "design-repair/design",
    "qa-preparation-repair/qa-preparation",
    "frontend-repair/frontend",
    "backend-repair/backend",
    "qa-delivery-repair/qa-delivery",
)


def knowledge_context_artifact_contract() -> ArtifactContract:
    """Return the one product contract that ACWM must own at Stage boundaries."""

    return ArtifactContract(
        id=KNOWLEDGE_CONTEXT_CONTRACT_ID,
        version=KNOWLEDGE_CONTEXT_CONTRACT_VERSION,
        modalities=frozenset({ArtifactModality.TEXT, ArtifactModality.STRUCTURED}),
    )


def knowledge_context_artifact_contract_sha256() -> Sha256:
    return sha256_json(knowledge_context_artifact_contract().model_dump(mode="json"))
