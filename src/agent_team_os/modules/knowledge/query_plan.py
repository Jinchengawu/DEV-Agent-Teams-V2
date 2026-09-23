"""Knowledge 内部查询编译合同；不属于 ACWM Artifact，不含 Provider I/O。

资格哈希只证明内容身份。真实资格必须由本地 Port 核验固定 tokenizer、容量和
资格测试证据；本模块不提供伪装成真实 tokenizer 的字符计数默认实现。
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256, sha256_bytes, sha256_json


class QueryPlanError(ValueError):
    """安全稳定错误码；不包含输入正文或外部错误信息。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QueryBudget(_Frozen):
    unit_tokens: int = Field(gt=0)
    unit_bytes: int = Field(gt=0)
    max_units: int = Field(gt=0)
    total_tokens: int = Field(gt=0)
    total_bytes: int = Field(gt=0)
    # 单个完整 Query 的累计预算，包含恢复时已经接纳的缓存；没有隐式默认值。
    max_duration_ms: int = Field(gt=0)
    max_cache_bytes: int = Field(gt=0)
    overlap_tokens: int = Field(ge=0)
    overlap_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> QueryBudget:
        if (self.overlap_tokens > self.unit_tokens // 8
                or self.overlap_bytes > self.unit_bytes // 8):
            raise ValueError("overlap exceeds one eighth of unit budget")
        return self

    @property
    def budget_sha256(self) -> Sha256:
        return sha256_json(self.model_dump(mode="json"))


def check_query_execution_budget(
    budget: QueryBudget, *, elapsed_ms: int, cache_bytes: int,
) -> None:
    """执行层提供累计消耗；本函数不启动时钟、读缓存或授予额外预算。

    elapsed_ms 必须含此前尝试消耗，cache_bytes 必须含已接纳单元及拟写入内容。
    调用方须在单元 I/O 前后、缓存写入和最终发布前检查；这不是 Provider 抢占器。
    """
    if elapsed_ms < 0 or cache_bytes < 0:
        raise QueryPlanError("KNOWLEDGE_QUERY_EXECUTION_BUDGET_INVALID")
    if elapsed_ms > budget.max_duration_ms:
        raise QueryPlanError("KNOWLEDGE_QUERY_TIME_BUDGET_EXCEEDED")
    if cache_bytes > budget.max_cache_bytes:
        raise QueryPlanError("KNOWLEDGE_QUERY_CACHE_BUDGET_EXCEEDED")


class QueryInputQualification(_Frozen):
    schema_version: Literal["knowledge-query-input-qualification-v1"] = (
        "knowledge-query-input-qualification-v1"
    )
    status: Literal["qualified", "blocked"]
    evidence_kind: Literal["live", "deterministic"]
    base_qualification_sha256: Sha256
    model_digest: str = Field(min_length=1)
    embedding_adapter_revision: str = Field(min_length=1)
    measurement_identity_sha256: Sha256
    tokenizer_assets_sha256: Sha256
    tokenizer_contract: str = Field(min_length=1)
    capacity_evidence_sha256: Sha256
    qualification_receipt_sha256: Sha256
    budget: QueryBudget

    @property
    def qualification_sha256(self) -> Sha256:
        return sha256_json(self.model_dump(mode="json"))


class InputMeasurement(_Frozen):
    tokens: int = Field(ge=0)
    utf8_bytes: int = Field(ge=0)
    identity_sha256: Sha256


class InputMeasurementPort(Protocol):
    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        """本地验证全部固定资产、容量/测试证据与预算；文件存在/hash有效不够。"""
        ...

    def measure(self, text: str) -> InputMeasurement:
        """精确测量最终传输串，包含 tokenizer special tokens；禁止网络和启发式。"""
        ...


class QueryUnit(_Frozen):
    ordinal: int = Field(ge=0)
    source_sha256: Sha256
    start: int = Field(ge=0)
    primary_start: int = Field(ge=0)
    end: int = Field(gt=0)
    input_sha256: Sha256
    tokens: int = Field(ge=0)
    utf8_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> QueryUnit:
        if not self.start <= self.primary_start < self.end:
            raise ValueError("invalid codepoint offsets")
        return self

    def reconstruct(self, query: str) -> str:
        if sha256_bytes(query.encode("utf-8")) != self.source_sha256 or self.end > len(query):
            raise QueryPlanError("KNOWLEDGE_QUERY_SOURCE_MISMATCH")
        text = query[self.start:self.end]
        if sha256_bytes(text.encode("utf-8")) != self.input_sha256:
            raise QueryPlanError("KNOWLEDGE_QUERY_SOURCE_MISMATCH")
        return text


class QueryPlan(_Frozen):
    schema_version: Literal["knowledge-query-plan-v1"] = "knowledge-query-plan-v1"
    compiler_revision: Literal["coverage-codepoint-v1"] = "coverage-codepoint-v1"
    source_sha256: Sha256
    source_characters: int = Field(gt=0)
    qualification_sha256: Sha256
    binding_sha256: Sha256
    budget_sha256: Sha256
    units: tuple[QueryUnit, ...] = Field(min_length=1)
    total_tokens: int = Field(ge=0)
    total_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_coverage(self) -> QueryPlan:
        cursor = 0
        for ordinal, unit in enumerate(self.units):
            if (unit.ordinal != ordinal or unit.primary_start != cursor
                    or unit.source_sha256 != self.source_sha256):
                raise ValueError("invalid query coverage")
            cursor = unit.end
        if (cursor != self.source_characters
                or self.total_tokens != sum(u.tokens for u in self.units)
                or self.total_bytes != sum(u.utf8_bytes for u in self.units)):
            raise ValueError("invalid query totals")
        return self

    @property
    def plan_sha256(self) -> Sha256:
        return sha256_json(self.model_dump(mode="json"))


def compile_query_plan(
    query: str,
    qualification: QueryInputQualification | None,
    measurement: InputMeasurementPort | None,
    *,
    base_qualification_sha256: Sha256,
    model_digest: str,
    embedding_adapter_revision: str,
    binding_sha256: Sha256,
    allow_deterministic: bool = False,
) -> QueryPlan:
    """完整 preflight；只有全部单元及总预算通过才返回 Plan。

    query 已由既有查询构造器形成最终源串；不追加前后缀，不做 Unicode 规范化。
    边界优先级：整串、段落、句末、codepoint；每级枚举所有字节可容纳候选，
    不假定 token 数单调，不在第一个超 token 限制的候选处停止。
    """
    if (qualification is None or measurement is None or qualification.status != "qualified"
            or (qualification.evidence_kind == "deterministic" and not allow_deterministic)
            or qualification.base_qualification_sha256 != base_qualification_sha256
            or qualification.model_digest != model_digest
            or qualification.embedding_adapter_revision != embedding_adapter_revision):
        raise QueryPlanError("KNOWLEDGE_QUERY_INPUT_UNQUALIFIED")
    try:
        verified = measurement.verify_qualification(qualification)
    except Exception:
        verified = False
    if verified is not True:
        raise QueryPlanError("KNOWLEDGE_QUERY_INPUT_UNQUALIFIED")
    if not query:
        raise QueryPlanError("KNOWLEDGE_QUERY_EMPTY")
    invalid_encoding = False
    try:
        encoded = query.encode("utf-8")
        if len(encoded) > qualification.budget.total_bytes:
            raise QueryPlanError("KNOWLEDGE_QUERY_BUDGET_EXCEEDED")
        source_sha = sha256_bytes(encoded)
        byte_offsets = [0]
        for char in query:
            byte_offsets.append(byte_offsets[-1] + len(char.encode("utf-8")))
    except UnicodeError:
        invalid_encoding = True
    if invalid_encoding:
        raise QueryPlanError("KNOWLEDGE_QUERY_ENCODING_INVALID")
    budget = qualification.budget

    def measured(text: str) -> InputMeasurement:
        result = None
        try:
            result = measurement.measure(text)
        except Exception:
            result = None
        if result is None:
            raise QueryPlanError("KNOWLEDGE_QUERY_MEASUREMENT_INVALID") from None
        if (result.identity_sha256 != qualification.measurement_identity_sha256
                or result.utf8_bytes != len(text.encode("utf-8"))):
            raise QueryPlanError("KNOWLEDGE_QUERY_MEASUREMENT_INVALID")
        return result

    def fits(result: InputMeasurement) -> bool:
        return result.tokens <= budget.unit_tokens and result.utf8_bytes <= budget.unit_bytes

    units: list[QueryUnit] = []
    cursor = total_tokens = total_bytes = 0
    while cursor < len(query):
        if len(units) >= budget.max_units:
            raise QueryPlanError("KNOWLEDGE_QUERY_BUDGET_EXCEEDED")
        candidates: list[tuple[int, int]] = []
        for end in range(cursor + 1, len(query) + 1):
            if byte_offsets[end] - byte_offsets[cursor] > budget.unit_bytes:
                break  # UTF-8 bytes 单调；tokens 不单调。
            priority = (0 if end == len(query) else 1 if query[max(cursor, end - 2):end] == "\n\n"
                        else 2 if query[end - 1] in ".!?。！？\n" else 3)
            candidates.append((priority, -end))
        selected: tuple[int, InputMeasurement] | None = None
        for _, negative_end in sorted(candidates):
            end = -negative_end
            result = measured(query[cursor:end])
            if fits(result):
                selected = end, result
                break
        if selected is None:
            raise QueryPlanError("KNOWLEDGE_QUERY_CAPACITY_INSUFFICIENT")
        end, result = selected
        start = cursor
        # 优先保留主覆盖，再寻找最长可容纳的完整 codepoint 重叠。
        for overlap_start in range(cursor - 1, -1, -1):
            if byte_offsets[cursor] - byte_offsets[overlap_start] > budget.overlap_bytes:
                break
            overlap = measured(query[overlap_start:cursor])
            combined = measured(query[overlap_start:end])
            if overlap.tokens <= budget.overlap_tokens and fits(combined):
                start, result = overlap_start, combined
        total_tokens += result.tokens
        total_bytes += result.utf8_bytes
        if total_tokens > budget.total_tokens or total_bytes > budget.total_bytes:
            raise QueryPlanError("KNOWLEDGE_QUERY_BUDGET_EXCEEDED")
        units.append(QueryUnit(ordinal=len(units), source_sha256=source_sha, start=start,
                               primary_start=cursor, end=end,
                               input_sha256=sha256_bytes(query[start:end].encode("utf-8")),
                               tokens=result.tokens, utf8_bytes=result.utf8_bytes))
        cursor = end
    return QueryPlan(source_sha256=source_sha, source_characters=len(query),
                     qualification_sha256=qualification.qualification_sha256,
                     binding_sha256=binding_sha256, budget_sha256=budget.budget_sha256,
                     units=tuple(units), total_tokens=total_tokens, total_bytes=total_bytes)
