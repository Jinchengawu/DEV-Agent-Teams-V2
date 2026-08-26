from __future__ import annotations

from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from .domain import (
    RuntimeExtension,
    RuntimeExtensionInstall,
    RuntimeExtensionRequirement,
)
from .repository import SQLiteRuntimeExtensionRepository

_ALLOWED_PERMISSIONS = frozenset(
    {
        "artifact:write",
        "resource:read",
        "workspace:read",
        "tool:invoke:design",
    }
)


class RuntimeExtensionCatalog:
    """Own installed extension inventory and fail-closed qualification facts."""

    def __init__(self, repository: SQLiteRuntimeExtensionRepository) -> None:
        self.repository = repository

    def install(self, request: RuntimeExtensionInstall, *, actor_id: str) -> RuntimeExtension:
        now = datetime.now(UTC)
        extension = RuntimeExtension(
            id=request.id,
            name=request.name,
            kind=request.kind,
            version_label=request.version,
            source_uri=request.source_uri,
            revision_sha256=request.revision_sha256,
            requested_permissions=request.requested_permissions,
            status="installed",
            version=1,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.repository.create(extension)
        except Exception as error:
            if "UNIQUE" in str(error):
                raise ProductError(
                    code="RUNTIME_EXTENSION_EXISTS",
                    title="运行扩展已经安装",
                    detail=f"扩展 {request.id} 已存在。",
                    repair="使用新的扩展 ID，或保留现有不可变安装记录。",
                    status_code=409,
                ) from error
            raise

    def list(self) -> tuple[RuntimeExtension, ...]:
        return self.repository.list()

    def get(self, extension_id: str) -> RuntimeExtension:
        try:
            return self.repository.get(extension_id)
        except KeyError as error:
            raise ProductError(
                code="RUNTIME_EXTENSION_NOT_FOUND",
                title="运行扩展不存在",
                detail=f"没有找到扩展 {extension_id}。",
                repair="刷新扩展目录后重新选择。",
                status_code=404,
            ) from error

    def qualify(self, extension_id: str, *, expected_version: int) -> RuntimeExtension:
        current = self.get(extension_id)
        if current.version != expected_version:
            raise ProductError(
                code="RUNTIME_EXTENSION_VERSION_CONFLICT",
                title="运行扩展版本冲突",
                detail="扩展安装事实已被其他操作更新。",
                repair="刷新扩展详情后重新执行资格检查。",
                status_code=409,
            )
        errors: list[str] = []
        if not set(current.requested_permissions) <= _ALLOWED_PERMISSIONS:
            errors.append("EXTENSION_PERMISSION_NOT_ALLOWED")
        qualification_hash = sha256_json(
            {
                "id": current.id,
                "kind": current.kind,
                "version": current.version_label,
                "source_uri": current.source_uri,
                "revision_sha256": current.revision_sha256,
                "requested_permissions": sorted(current.requested_permissions),
                "policy_version": "1",
            }
        )
        updated = current.model_copy(
            update={
                "status": "failed" if errors else "qualified",
                "qualification_sha256": None if errors else qualification_hash,
                "qualification_errors": tuple(errors),
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap(current.version, updated):
            return self.qualify(extension_id, expected_version=expected_version)
        return updated

    def resolve(self, raw_requirement: dict[str, object]) -> dict[str, object]:
        requirement = RuntimeExtensionRequirement.model_validate(raw_requirement)
        extension = self.get(requirement.id)
        if extension.status != "qualified" or extension.qualification_sha256 is None:
            raise ProductError(
                code="RUNTIME_EXTENSION_NOT_QUALIFIED",
                title="运行扩展尚未通过资格检查",
                detail=f"扩展 {extension.id} 当前不可进入 Agent 会话。",
                repair="由管理员完成安装与资格检查。",
                status_code=409,
            )
        if extension.kind != requirement.kind or not _matches(
            extension.version_label, requirement.version
        ):
            raise ProductError(
                code="RUNTIME_EXTENSION_VERSION_INCOMPATIBLE",
                title="运行扩展版本不兼容",
                detail=f"扩展 {extension.id} 不满足 {requirement.version}。",
                repair="安装符合角色要求的扩展版本并重新资格检查。",
                status_code=409,
            )
        return {
            "id": extension.id,
            "kind": extension.kind,
            "version": extension.version_label,
            "source_uri": extension.source_uri,
            "revision_sha256": extension.revision_sha256,
            "qualification_sha256": extension.qualification_sha256,
            "permissions": extension.requested_permissions,
        }


def _matches(version: str, constraint: str) -> bool:
    current = _version(version)
    for raw in constraint.split(","):
        clause = raw.strip()
        operator = next(
            (item for item in (">=", "<=", "==", ">", "<") if clause.startswith(item)),
            "==",
        )
        expected = _version(clause.removeprefix(operator))
        if operator == ">=" and current < expected:
            return False
        if operator == "<=" and current > expected:
            return False
        if operator == ">" and current <= expected:
            return False
        if operator == "<" and current >= expected:
            return False
        if operator == "==" and current != expected:
            return False
    return True


def _version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) == 1:
        parts.extend(("0", "0"))
    elif len(parts) == 2:
        parts.append("0")
    try:
        return tuple(int(item) for item in parts)  # type: ignore[return-value]
    except ValueError as error:
        raise ValueError(f"unsupported extension version: {value}") from error
