"""显式 Deterministic 查询夹具；不进入产品依赖，不证明真实 tokenizer/容量。"""
from __future__ import annotations

from agent_team_os.modules.knowledge.index_application import KnowledgeIndexManager
from agent_team_os.modules.knowledge.index_domain import EmbeddingQualificationSnapshot
from agent_team_os.modules.knowledge.query_plan import (
    InputMeasurement,
    QueryBudget,
    QueryInputQualification,
)
from agent_team_os.shared.hashes import Sha256, sha256_bytes

FIXTURE_IDENTITY = sha256_bytes(b"query-test-only-codepoint-measurement-v1")


class DeterministicQueryMeasurement:
    """只接纳本 helper 显式登记的合成资格，不把任意哈希当资格证据。"""

    def __init__(self) -> None:
        self.registered: set[Sha256] = set()

    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        return (qualification.evidence_kind == "deterministic"
                and qualification.qualification_sha256 in self.registered)

    def measure(self, text: str) -> InputMeasurement:
        return InputMeasurement(tokens=len(text), utf8_bytes=len(text.encode("utf-8")),
                                identity_sha256=FIXTURE_IDENTITY)


def install_deterministic_query_qualification(
    indexes: KnowledgeIndexManager,
    base: EmbeddingQualificationSnapshot,
    *,
    budget: QueryBudget | None = None,
) -> QueryInputQualification:
    """绑定实际 fixture base；调用者必须把返回 hash 写入新 RetrievalPolicy。

    下列数值只是合成测试能力，不是 bge-m3/Ollama 的可执行上限。
    """
    measurement = indexes.query_measurement
    if not isinstance(measurement, DeterministicQueryMeasurement):
        measurement = DeterministicQueryMeasurement()
    query = QueryInputQualification(
        status="qualified", evidence_kind="deterministic",
        base_qualification_sha256=base.qualification_sha256, model_digest=base.model_digest,
        embedding_adapter_revision=base.adapter_revision,
        measurement_identity_sha256=FIXTURE_IDENTITY,
        tokenizer_assets_sha256=FIXTURE_IDENTITY,
        tokenizer_contract="test-only-codepoint-identity-v1",
        capacity_evidence_sha256=FIXTURE_IDENTITY,
        qualification_receipt_sha256=FIXTURE_IDENTITY,
        budget=budget or QueryBudget(
            unit_tokens=10000, unit_bytes=40000, max_units=100,
            total_tokens=100000, total_bytes=400000,
            overlap_tokens=0, overlap_bytes=0,
            max_duration_ms=60000, max_cache_bytes=10000000,
        ),
    )
    measurement.registered.add(query.qualification_sha256)
    indexes.query_repository.put_qualification(query)
    indexes.query_measurement = measurement
    indexes.allow_deterministic_queries = True
    return query
