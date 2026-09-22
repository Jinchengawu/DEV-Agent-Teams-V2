"""Resolve the immutable product-resource root used by the preview runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PRODUCT_ROOT_ENV = "AGENT_TEAM_OS_PRODUCT_ROOT"
_REQUIRED_CONFIG = (
    "capabilities.yaml",
    "framework-lock.json",
    "journeys.yaml",
    "method-packs-v050.json",
)
_EVALUATION_DATASET = Path("evaluation/datasets/agent-team-os-mvp/1.3.0")


class ProductRootError(RuntimeError):
    """Raised when an explicitly selected product root is incomplete."""


def resolve_product_root(
    explicit: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve and validate a product root.

    An argument or environment value is an explicit operator choice and therefore
    fails closed. Only the absence of both selects the source-checkout fallback.
    """

    values = os.environ if environ is None else environ
    configured = values.get(PRODUCT_ROOT_ENV)
    is_explicit = explicit is not None or configured is not None
    if explicit is not None:
        root = explicit
    elif configured is not None:
        if not configured.strip():
            raise ProductRootError(f"{PRODUCT_ROOT_ENV} 不得为空。")
        root = Path(configured)
    else:
        root = Path(__file__).parents[2]
    resolved = root.expanduser().resolve()
    _validate_product_root(resolved, require_built_console=is_explicit)
    return resolved


def _validate_product_root(root: Path, *, require_built_console: bool) -> None:
    if not root.is_dir():
        raise ProductRootError(f"Product Root 不是可读目录：{root}")
    required = [root / "config" / name for name in _REQUIRED_CONFIG]
    required.append(root / "migrations")
    required.append(root / "console")
    required.extend(
        root / _EVALUATION_DATASET / name
        for name in ("manifest.json", "schema.json", "cases.jsonl")
    )
    if require_built_console:
        required.append(root / "console" / "dist" / "index.html")
    missing = [path.relative_to(root).as_posix() for path in required if not path.exists()]
    if missing:
        raise ProductRootError(f"Product Root 缺少必需资源：{', '.join(missing)}")
    if not any((root / "migrations").glob("*.sql")):
        raise ProductRootError("Product Root 缺少 migrations/*.sql。")
