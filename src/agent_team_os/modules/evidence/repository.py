from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...shared.ids import new_id
from .domain import EvidenceRecord, EvidenceStatus, EvidenceVerificationRecord


class SQLiteEvidenceRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def append(self, record: EvidenceRecord) -> EvidenceRecord:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evidence_records(
                id,project_id,delivery_id,kind,source_kind,source_id,producer_identity,content_sha256,
                status,payload_json,created_at,verified_at,verification_error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.id,
                    record.project_id,
                    record.delivery_id,
                    record.kind.value,
                    record.source_kind,
                    record.source_id,
                    record.producer_identity,
                    record.content_sha256,
                    record.status.value,
                    json.dumps(record.payload, ensure_ascii=False, separators=(",", ":")),
                    record.created_at.isoformat(),
                    record.verified_at.isoformat() if record.verified_at else None,
                    record.verification_error,
                ),
            )
        found = self.get(record.id)
        if found is None:
            raise RuntimeError("evidence insert was not readable")
        return found

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                _SELECT + " WHERE evidence_records.id=?", (evidence_id,)
            ).fetchone()
        return None if row is None else _record(row)

    def list(
        self, delivery_id: str | None = None, project_id: str | None = None
    ) -> tuple[EvidenceRecord, ...]:
        sql = _SELECT
        clauses: list[str] = []
        values: list[str] = []
        if delivery_id is not None:
            clauses.append("evidence_records.delivery_id=?")
            values.append(delivery_id)
        if project_id is not None:
            clauses.append("evidence_records.project_id=?")
            values.append(project_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY evidence_records.created_at DESC,evidence_records.kind"
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(sql, tuple(values)).fetchall()
        return tuple(_record(row) for row in rows)

    def append_verification(
        self, evidence_id: str, status: EvidenceStatus, error: str | None
    ) -> EvidenceRecord:
        now = datetime.now(UTC)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO evidence_verifications(id,evidence_id,status,error,verified_at)
                VALUES(?,?,?,?,?)""",
                (new_id(), evidence_id, status.value, error, now.isoformat()),
            )
        found = self.get(evidence_id)
        if found is None:
            raise KeyError(evidence_id)
        return found

    def list_verifications(self, evidence_id: str) -> tuple[EvidenceVerificationRecord, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT id,evidence_id,status,error,verified_at
                FROM evidence_verifications WHERE evidence_id=?
                ORDER BY verified_at DESC,id DESC""",
                (evidence_id,),
            ).fetchall()
        return tuple(
            EvidenceVerificationRecord.model_validate(
                {
                    "id": row[0],
                    "evidence_id": row[1],
                    "status": row[2],
                    "error": row[3],
                    "verified_at": row[4],
                }
            )
            for row in rows
        )


_SELECT = """SELECT evidence_records.id,evidence_records.project_id,
evidence_records.delivery_id,evidence_records.kind,
evidence_records.source_kind,evidence_records.source_id,evidence_records.producer_identity,
evidence_records.content_sha256,
COALESCE(latest.status,evidence_records.status),evidence_records.payload_json,
evidence_records.created_at,latest.verified_at,
COALESCE(latest.error,evidence_records.verification_error)
FROM evidence_records
LEFT JOIN evidence_verifications latest ON latest.id=(
  SELECT id FROM evidence_verifications verification
  WHERE verification.evidence_id=evidence_records.id
  ORDER BY verification.verified_at DESC LIMIT 1
)"""


def _record(row: tuple[object, ...]) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "id": row[0],
            "project_id": row[1],
            "delivery_id": row[2],
            "kind": row[3],
            "source_kind": row[4],
            "source_id": row[5],
            "producer_identity": row[6],
            "content_sha256": row[7],
            "status": row[8],
            "payload": json.loads(str(row[9])),
            "created_at": row[10],
            "verified_at": row[11],
            "verification_error": row[12],
        }
    )
