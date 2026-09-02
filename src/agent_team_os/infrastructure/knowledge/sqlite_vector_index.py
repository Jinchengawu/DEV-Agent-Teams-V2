from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

import sqlite_vec  # type: ignore[import-untyped]

from ...modules.knowledge.index_ports import (
    VectorIndexDescriptor,
    VectorIndexRecord,
    VectorSearchMatch,
)


class SQLiteVectorIndexAdapter:
    """Qualified sqlite-vec 0.1.9 adapter for immutable Hybrid Index files."""

    adapter_revision = "sqlite-vec-vector-index-v1"

    def describe(self) -> VectorIndexDescriptor:
        with sqlite3.connect(":memory:") as connection:
            _load_sqlite_vec(connection)
            row = connection.execute("SELECT vec_version()").fetchone()
        if row is None:
            raise RuntimeError("KNOWLEDGE_VECTOR_INDEX_VERSION_PROBE_FAILED")
        return VectorIndexDescriptor(
            engine_name="sqlite-vec",
            engine_version=str(row[0]).removeprefix("v"),
            adapter_revision=self.adapter_revision,
        )

    def build(
        self,
        path: Path,
        batches: Iterable[tuple[VectorIndexRecord, ...]],
        *,
        dimension: int,
    ) -> int:
        count = 0
        with sqlite3.connect(path) as connection:
            _load_sqlite_vec(connection)
            connection.executescript(
                """CREATE TABLE chunk_vectors(
                    row_id INTEGER PRIMARY KEY,
                    chunk_id TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    embedding BLOB NOT NULL
                );
                CREATE INDEX chunk_vector_source_idx
                    ON chunk_vectors(source_id,row_id);
                """
            )
            for batch in batches:
                if any(len(record.embedding) != dimension for record in batch):
                    raise RuntimeError("KNOWLEDGE_EMBEDDING_DIMENSION_DRIFT")
                connection.executemany(
                    """INSERT INTO chunk_vectors(row_id,chunk_id,source_id,embedding)
                    VALUES(?,?,?,?)""",
                    (
                        (
                            record.row_id,
                            record.chunk_id,
                            record.source_id,
                            sqlite_vec.serialize_float32(record.embedding),
                        )
                        for record in batch
                    ),
                )
                count += len(batch)
        return count

    def search(
        self,
        path: Path,
        query_vector: tuple[float, ...],
        allowed_source_ids: tuple[str, ...],
        *,
        limit: int,
    ) -> tuple[VectorSearchMatch, ...]:
        if not allowed_source_ids:
            return ()
        placeholders = ",".join("?" for _ in allowed_source_ids)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=1")
            _load_sqlite_vec(connection)
            rows = connection.execute(
                f"""SELECT row_id,chunk_id,
                vec_distance_cosine(embedding,?) AS vector_distance
                FROM chunk_vectors
                WHERE source_id IN ({placeholders})
                ORDER BY vector_distance,chunk_id LIMIT ?""",  # noqa: S608
                (
                    sqlite_vec.serialize_float32(query_vector),
                    *allowed_source_ids,
                    limit,
                ),
            ).fetchall()
        return tuple(
            VectorSearchMatch(
                row_id=int(row["row_id"]),
                chunk_id=str(row["chunk_id"]),
                distance=float(row["vector_distance"]),
            )
            for row in rows
            if row["vector_distance"] is not None
        )

    def verify(
        self,
        path: Path,
        *,
        dimension: int,
        expected_count: int,
    ) -> None:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=1")
            _load_sqlite_vec(connection)
            count = int(connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0])
            probe = connection.execute(
                "SELECT vec_length(embedding) FROM chunk_vectors LIMIT 1"
            ).fetchone()
        if count != expected_count:
            raise RuntimeError("KNOWLEDGE_INDEX_VECTOR_COUNT_MISMATCH")
        if expected_count and (probe is None or int(probe[0]) != dimension):
            raise RuntimeError("KNOWLEDGE_INDEX_VECTOR_PROBE_FAILED")


def _load_sqlite_vec(connection: sqlite3.Connection) -> None:
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
