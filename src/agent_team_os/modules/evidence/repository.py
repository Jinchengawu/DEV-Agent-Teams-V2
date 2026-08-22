from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...shared.ids import new_id
from .domain import EvidenceRecord, EvidenceStatus


class SQLiteEvidenceRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def append(self, record: EvidenceRecord) -> EvidenceRecord:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evidence_records(
                id,delivery_id,kind,source_kind,source_id,producer_identity,content_sha256,
                status,payload_json,created_at,verified_at,verification_error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.id,
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

    def list(self, delivery_id: str | None = None) -> tuple[EvidenceRecord, ...]:
        sql = _SELECT
        parameters: tuple[str, ...] = ()
        if delivery_id is not None:
            sql += " WHERE evidence_records.delivery_id=?"
            parameters = (delivery_id,)
        sql += " ORDER BY evidence_records.created_at DESC,evidence_records.kind"
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(sql, parameters).fetchall()
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


_SELECT = """SELECT evidence_records.id,evidence_records.delivery_id,evidence_records.kind,
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
            "delivery_id": row[1],
            "kind": row[2],
            "source_kind": row[3],
            "source_id": row[4],
            "producer_identity": row[5],
            "content_sha256": row[6],
            "status": row[7],
            "payload": json.loads(str(row[8])),
            "created_at": row[9],
            "verified_at": row[10],
            "verification_error": row[11],
        }
    )

