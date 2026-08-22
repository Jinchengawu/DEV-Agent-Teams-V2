from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProblemDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    title: str
    detail: str
    repair: str
    trace_id: str
    expected_version: int | None = None
    actual_version: int | None = None


class ProductError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        title: str,
        detail: str,
        repair: str,
        status_code: int = 409,
        expected_version: int | None = None,
        actual_version: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.title = title
        self.detail = detail
        self.repair = repair
        self.status_code = status_code
        self.expected_version = expected_version
        self.actual_version = actual_version

    def problem(self, trace_id: str) -> ProblemDetail:
        return ProblemDetail(
            code=self.code,
            title=self.title,
            detail=self.detail,
            repair=self.repair,
            trace_id=trace_id,
            expected_version=self.expected_version,
            actual_version=self.actual_version,
        )
