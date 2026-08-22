from __future__ import annotations

from datetime import UTC, datetime

from ...shared.errors import ProductError
from .domain import AppSettings, AppSettingsPatch
from .ports import SettingsRepository


class SettingsManager:
    def __init__(self, repository: SettingsRepository) -> None:
        self.repository = repository

    def get(self) -> AppSettings:
        return self.repository.get()

    def patch(self, request: AppSettingsPatch) -> AppSettings:
        current = self.repository.get()
        if current.version != request.expected_version:
            raise ProductError(
                code="SETTINGS_VERSION_CONFLICT",
                title="设置版本冲突",
                detail="设置已被其他操作更新。",
                repair="刷新设置后重新保存。",
                expected_version=request.expected_version,
                actual_version=current.version,
            )
        changes = request.model_dump(exclude_none=True, exclude={"expected_version"})
        updated = current.model_copy(
            update={
                **changes,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap(current.version, updated):
            latest = self.repository.get()
            raise ProductError(
                code="SETTINGS_VERSION_CONFLICT",
                title="设置版本冲突",
                detail="设置已被其他操作更新。",
                repair="刷新设置后重新保存。",
                expected_version=current.version,
                actual_version=latest.version,
            )
        return updated

