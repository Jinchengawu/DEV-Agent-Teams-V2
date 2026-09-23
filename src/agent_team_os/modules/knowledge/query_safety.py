"""查询失败的安全投影，不接受 Provider 正文、请求文本或凭据引用。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ...shared.hashes import Sha256


class QuerySafeError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: Literal[
        "KNOWLEDGE_QUERY_INPUT_LIMIT",
        "KNOWLEDGE_QUERY_PROVIDER_FAILED",
        "KNOWLEDGE_QUERY_BUDGET_EXCEEDED",
    ]
    category: Literal[
        "input_limit",
        "invalid_request",
        "rate_limited",
        "provider_error",
        "timeout",
        "transport",
        "invalid_response",
        "unknown",
        "budget",
    ]
    correlation_id: UUID
    http_status: int | None = Field(default=None, ge=100, le=599)
    unit: int = Field(ge=0)
    plan_sha256: Sha256
    qualification_sha256: Sha256
    budget_sha256: Sha256
    measured_tokens: int = Field(ge=0)
    allowed_tokens: int = Field(ge=0)
    measured_bytes: int = Field(ge=0)
    allowed_bytes: int = Field(ge=0)
    attempt: int = Field(ge=1)
    occurred_at: datetime
    duration_ms: int = Field(ge=0)
    measured_duration_ms: int | None = Field(default=None, ge=0)
    allowed_duration_ms: int | None = Field(default=None, ge=0)
    measured_cache_bytes: int | None = Field(default=None, ge=0)
    allowed_cache_bytes: int | None = Field(default=None, ge=0)
