from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .index_domain import EmbeddingModelDescriptor


class EmbeddingFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class EmbeddingPort(Protocol):
    adapter_revision: str

    def describe(self, model_name: str) -> EmbeddingModelDescriptor: ...

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model_name: str,
        truncate: bool,
    ) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True, slots=True)
class VectorIndexDescriptor:
    engine_name: str
    engine_version: str
    adapter_revision: str


@dataclass(frozen=True, slots=True)
class VectorIndexRecord:
    row_id: int
    chunk_id: str
    source_id: str
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class VectorSearchMatch:
    row_id: int
    chunk_id: str
    distance: float


class VectorIndexPort(Protocol):
    """Owns vector-engine storage and search behind the Knowledge module port."""

    adapter_revision: str

    def describe(self) -> VectorIndexDescriptor: ...

    def build(
        self,
        path: Path,
        batches: Iterable[tuple[VectorIndexRecord, ...]],
        *,
        dimension: int,
    ) -> int: ...

    def search(
        self,
        path: Path,
        query_vector: tuple[float, ...],
        allowed_source_ids: tuple[str, ...],
        *,
        limit: int,
    ) -> tuple[VectorSearchMatch, ...]: ...

    def verify(
        self,
        path: Path,
        *,
        dimension: int,
        expected_count: int,
    ) -> None: ...
