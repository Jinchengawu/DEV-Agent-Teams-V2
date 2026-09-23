"""内部 Query Plan 串行执行与恢复；仅消费已冻结身份，不选择 Active Index。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ...shared.hashes import Sha256, sha256_bytes
from ..artifacts.storage import ArtifactReference, ContentAddressedArtifactStorage
from .index_domain import KnowledgeRetrievalHit
from .index_ports import EmbeddingFailure
from .query_fusion import contribution_audit
from .query_plan import QueryInputQualification, QueryPlan
from .query_repository import QueryExecutionIdentity, SQLiteKnowledgeQueryRepository
from .query_safety import QuerySafeError


class _UnitCache(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_key: Sha256
    plan_sha256: Sha256
    ordinal: int
    hits: tuple[KnowledgeRetrievalHit, ...]


class QueryExecutionBudgetError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class QueryPlanExecutor:
    def __init__(
        self,
        repository: SQLiteKnowledgeQueryRepository,
        storage: ContentAddressedArtifactStorage,
        *,
        lease_ttl: timedelta,
        max_cache_bytes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_ttl <= timedelta(0) or max_cache_bytes < 1:
            raise ValueError("KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID")
        self.repository = repository
        self.storage = storage
        self.lease_ttl = lease_ttl
        self.max_cache_bytes = max_cache_bytes
        self.clock = clock or (lambda: datetime.now(UTC))

    def _budget_error(
        self,
        key: str,
        plan: QueryPlan,
        qualification: QueryInputQualification,
        fence: int | None,
        ordinal: int,
        code: str,
        cache_bytes: int,
        duration_ms: int,
        allowed_duration_ms: int,
    ) -> QueryExecutionBudgetError:
        if fence is not None:
            unit = plan.units[ordinal]
            safe = QuerySafeError(
                code="KNOWLEDGE_QUERY_BUDGET_EXCEEDED",
                category="budget",
                correlation_id=uuid4(),
                unit=ordinal,
                plan_sha256=plan.plan_sha256,
                qualification_sha256=plan.qualification_sha256,
                budget_sha256=plan.budget_sha256,
                measured_tokens=unit.tokens,
                allowed_tokens=qualification.budget.unit_tokens,
                measured_bytes=unit.utf8_bytes,
                allowed_bytes=qualification.budget.unit_bytes,
                attempt=fence,
                occurred_at=self.clock(),
                duration_ms=duration_ms,
                measured_duration_ms=duration_ms,
                allowed_duration_ms=allowed_duration_ms,
                measured_cache_bytes=cache_bytes,
                allowed_cache_bytes=self.max_cache_bytes,
            )
            try:
                self.repository.put_safe_error(key, fence=fence, error=safe, now=self.clock())
                self.repository.release(key, fence=fence, now=self.clock())
            except RuntimeError as error:
                if str(error) != "KNOWLEDGE_QUERY_FENCE_CONFLICT":
                    raise
        return QueryExecutionBudgetError(code)

    def _read(self, digest: str) -> bytes:
        digest = Sha256.validate(digest)
        reference = ArtifactReference(
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            media_type="application/json",
            size_bytes=0,
        )
        path = self.storage.path_for(reference)
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise RuntimeError("KNOWLEDGE_QUERY_CACHE_UNSAFE")
        reference = reference.model_copy(update={"size_bytes": path.stat().st_size})
        return self.storage.get_bytes(reference, max_bytes=self.max_cache_bytes)

    def _write(self, payload: bytes) -> Sha256:
        if len(payload) > self.max_cache_bytes:
            raise RuntimeError("KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED")
        digest = sha256_bytes(payload)
        reference = ArtifactReference(
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            media_type="application/json",
            size_bytes=len(payload),
        )
        path = self.storage.path_for(reference)
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise RuntimeError("KNOWLEDGE_QUERY_CACHE_UNSAFE")
        return self.storage.put_bytes(payload, media_type="application/json").sha256

    def execute(
        self,
        identity: QueryExecutionIdentity,
        plan: QueryPlan,
        qualification: QueryInputQualification,
        query: str,
        execute_unit: Callable[[str], tuple[KnowledgeRetrievalHit, ...]],
        admission: Callable[[], None],
        *,
        max_duration_seconds: float,
    ) -> tuple[tuple[KnowledgeRetrievalHit, ...], ...]:
        if max_duration_seconds <= 0:
            raise ValueError("KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID")
        if (
            max_duration_seconds * 1000 > qualification.budget.max_duration_ms
            or self.max_cache_bytes > qualification.budget.max_cache_bytes
        ):
            raise ValueError("KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID")
        deadline = time.monotonic() + max_duration_seconds
        admission()
        if (
            qualification.status != "qualified"
            or qualification.qualification_sha256 != plan.qualification_sha256
            or qualification.budget.budget_sha256 != plan.budget_sha256
            or sha256_bytes(query.encode()) != plan.source_sha256
            or identity.input_sha256 != plan.source_sha256
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_EXECUTION_IDENTITY_CONFLICT")
        for unit in plan.units:
            unit.reconstruct(query)
            if (
                unit.tokens > qualification.budget.unit_tokens
                or unit.utf8_bytes > qualification.budget.unit_bytes
            ):
                raise RuntimeError("KNOWLEDGE_QUERY_BUDGET_EXCEEDED")
        if (
            len(plan.units) > qualification.budget.max_units
            or plan.total_tokens > qualification.budget.total_tokens
            or plan.total_bytes > qualification.budget.total_bytes
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_BUDGET_EXCEEDED")
        self.repository.assert_admitted(identity, plan)
        record = self.repository.get_for_execution(identity)
        key = identity.execution_key
        if any(
            error.plan_sha256 == plan.plan_sha256
            for error in self.repository.list_safe_errors(identity.preparation_run_id)
        ):
            raise RuntimeError("KNOWLEDGE_QUERY_FAILED")
        receipts = self.repository.unit_receipts(key)
        elapsed = self.repository.elapsed_ms(key, now=self.clock())
        remaining = max_duration_seconds - elapsed / 1000
        if remaining <= 0:
            raise RuntimeError("KNOWLEDGE_QUERY_TIME_BUDGET_EXCEEDED")
        deadline = min(deadline, time.monotonic() + remaining)
        cached_payloads = {ordinal: self._read(digest) for ordinal, digest in receipts.items()}
        cache_bytes = sum(len(payload) for payload in cached_payloads.values())
        if cache_bytes > self.max_cache_bytes:
            raise RuntimeError("KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED")
        fence = (
            None
            if record.artifact_sha256
            else self.repository.claim(
                key,
                owner=str(uuid4()),
                now=self.clock(),
                ttl=self.lease_ttl,
            )
        )
        result: list[tuple[KnowledgeRetrievalHit, ...]] = []
        for unit in plan.units:
            if time.monotonic() >= deadline:
                raise self._budget_error(
                    key,
                    plan,
                    qualification,
                    fence,
                    unit.ordinal,
                    "KNOWLEDGE_QUERY_TIME_BUDGET_EXCEEDED",
                    cache_bytes,
                    self.repository.elapsed_ms(key, now=self.clock()),
                    int(max_duration_seconds * 1000),
                )
            admission()
            if unit.ordinal in receipts:
                cache = _UnitCache.model_validate_json(cached_payloads[unit.ordinal])
                if (
                    cache.execution_key != key
                    or cache.plan_sha256 != plan.plan_sha256
                    or cache.ordinal != unit.ordinal
                ):
                    raise RuntimeError("KNOWLEDGE_QUERY_CACHE_IDENTITY_CONFLICT")
                hits = cache.hits
            else:
                if fence is None:
                    raise RuntimeError("KNOWLEDGE_QUERY_INCOMPLETE")
                started = time.monotonic()
                failure = None
                try:
                    hits = execute_unit(unit.reconstruct(query))
                except EmbeddingFailure as error:
                    transient = error.category in {"timeout", "transport"}
                    code = (
                        "KNOWLEDGE_OLLAMA_UNAVAILABLE"
                        if transient
                        else "KNOWLEDGE_QUERY_INPUT_LIMIT"
                        if error.category == "input_limit"
                        else "KNOWLEDGE_QUERY_PROVIDER_FAILED"
                    )
                    if not transient:
                        safe = QuerySafeError(
                            code="KNOWLEDGE_QUERY_INPUT_LIMIT"
                            if error.category == "input_limit"
                            else "KNOWLEDGE_QUERY_PROVIDER_FAILED",
                            category=error.category,
                            correlation_id=uuid4(),
                            http_status=error.http_status,
                            unit=unit.ordinal,
                            plan_sha256=plan.plan_sha256,
                            qualification_sha256=plan.qualification_sha256,
                            budget_sha256=plan.budget_sha256,
                            measured_tokens=unit.tokens,
                            allowed_tokens=qualification.budget.unit_tokens,
                            measured_bytes=unit.utf8_bytes,
                            allowed_bytes=qualification.budget.unit_bytes,
                            attempt=fence,
                            occurred_at=self.clock(),
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        self.repository.put_safe_error(
                            key, fence=fence, error=safe, now=self.clock()
                        )
                    self.repository.release(key, fence=fence, now=self.clock())
                    failure = EmbeddingFailure(
                        code,
                        "Knowledge 查询 Provider 调用失败",
                        http_status=error.http_status,
                        category=error.category,
                    )
                if failure is not None:
                    raise failure
                admission()
                cache = _UnitCache(
                    execution_key=Sha256(key),
                    plan_sha256=plan.plan_sha256,
                    ordinal=unit.ordinal,
                    hits=hits,
                )
                cache_payload = cache.model_dump_json().encode()
                if cache_bytes + len(cache_payload) > self.max_cache_bytes:
                    raise self._budget_error(
                        key,
                        plan,
                        qualification,
                        fence,
                        unit.ordinal,
                        "KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED",
                        cache_bytes + len(cache_payload),
                        self.repository.elapsed_ms(key, now=self.clock()),
                        int(max_duration_seconds * 1000),
                    )
                digest = self._write(cache_payload)
                self.repository.put_unit_receipt(
                    key, fence=fence, ordinal=unit.ordinal, result_sha256=digest, now=self.clock()
                )
                receipts[unit.ordinal] = digest
                cache_bytes += len(cache_payload)
            result.append(hits)
        admission()
        if time.monotonic() >= deadline:
            raise self._budget_error(
                key,
                plan,
                qualification,
                fence,
                len(plan.units) - 1,
                "KNOWLEDGE_QUERY_TIME_BUDGET_EXCEEDED",
                cache_bytes,
                self.repository.elapsed_ms(key, now=self.clock()),
                int(max_duration_seconds * 1000),
            )
        manifest = {
            "execution_key": key,
            "plan_sha256": str(plan.plan_sha256),
            "unit_results": [receipts[u.ordinal] for u in plan.units],
            "query_contributions": contribution_audit(
                tuple(result), tuple(str(u.input_sha256) for u in plan.units)
            ),
        }
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        if cache_bytes + len(payload) > self.max_cache_bytes:
            raise self._budget_error(
                key,
                plan,
                qualification,
                fence,
                len(plan.units) - 1,
                "KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED",
                cache_bytes + len(payload),
                self.repository.elapsed_ms(key, now=self.clock()),
                int(max_duration_seconds * 1000),
            )
        if record.artifact_sha256:
            if self._read(record.artifact_sha256) != payload:
                raise RuntimeError("KNOWLEDGE_QUERY_CACHE_IDENTITY_CONFLICT")
        else:
            assert fence is not None
            digest = self._write(payload)
            self.repository.complete(key, fence=fence, artifact_sha256=digest, now=self.clock())
        return tuple(result)
