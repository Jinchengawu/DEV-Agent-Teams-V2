from __future__ import annotations

from typing import Protocol

from .domain import AppSettings


class SettingsRepository(Protocol):
    def get(self) -> AppSettings: ...

    def compare_and_swap(self, expected_version: int, updated: AppSettings) -> bool: ...

