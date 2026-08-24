from __future__ import annotations

from importlib import import_module
from typing import Literal

from pydantic import BaseModel, ConfigDict

RuntimeType = Literal["hermes-acp", "hermes-http", "codex-cli"]


class RuntimeAdapterDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: str | None
    runtime_type: RuntimeType
    features: tuple[str, ...]
    available: bool
    features_source: Literal["installed-acwm-adapter-manifest"] = (
        "installed-acwm-adapter-manifest"
    )
    error_code: str | None = None


class RuntimeAdapterCatalog:
    _ADAPTERS: tuple[tuple[RuntimeType, str, str, str], ...] = (
        ("codex-cli", "acwm.adapters.codex_cli", "CodexCLICapabilityAdapter", "codex.cli"),
        ("hermes-acp", "acwm.adapters.hermes_acp", "HermesACPCapabilityAdapter", "hermes.acp"),
        ("hermes-http", "acwm.adapters.http_sync", "HttpSyncCapabilityAdapter", "http.sync"),
    )

    def list(self) -> tuple[RuntimeAdapterDescriptor, ...]:
        return tuple(self._inspect(*definition) for definition in self._ADAPTERS)

    def for_runtime(self, runtime_type: str) -> RuntimeAdapterDescriptor:
        for descriptor in self.list():
            if descriptor.runtime_type == runtime_type:
                if not descriptor.available:
                    raise ValueError(f"runtime adapter unavailable: {runtime_type}")
                return descriptor
        raise ValueError(f"unknown runtime adapter: {runtime_type}")

    @staticmethod
    def _inspect(
        runtime_type: RuntimeType,
        module_name: str,
        class_name: str,
        expected_adapter_id: str,
    ) -> RuntimeAdapterDescriptor:
        try:
            adapter = getattr(import_module(module_name), class_name)
            manifest = adapter.manifest
            if manifest.adapter_type != expected_adapter_id:
                raise ValueError("installed Adapter Manifest identity does not match catalog")
            return RuntimeAdapterDescriptor(
                id=manifest.adapter_type,
                version=manifest.adapter_version,
                runtime_type=runtime_type,
                features=tuple(sorted(feature.value for feature in manifest.features)),
                available=True,
            )
        except (ImportError, AttributeError, ValueError):
            return RuntimeAdapterDescriptor(
                id=expected_adapter_id,
                version=None,
                runtime_type=runtime_type,
                features=(),
                available=False,
                error_code="ACWM_ADAPTER_UNAVAILABLE",
            )
