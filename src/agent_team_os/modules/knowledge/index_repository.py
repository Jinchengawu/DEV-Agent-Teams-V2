from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .index_domain import (
    EmbeddingQualificationSnapshot,
    KnowledgeIndexProfileRevision,
    KnowledgeIndexRevision,
    KnowledgeRetrievalReceipt,
    RetrievalEvaluationPolicyRevision,
    RetrievalEvaluationReport,
    RetrievalPolicyRevision,
)


class SQLiteKnowledgeIndexRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def publish_index_profile(
        self, revision: KnowledgeIndexProfileRevision
    ) -> KnowledgeIndexProfileRevision | None:
        config = revision.model_dump(
            mode="json",
            exclude={"config_sha256", "published_by", "published_at"},
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO knowledge_index_profile_revisions(
                    id,display_name,config_json,config_sha256,published_by,published_at)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        revision.id,
                        revision.display_name,
                        _json(config),
                        revision.config_sha256,
                        revision.published_by,
                        revision.published_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return revision

    def get_index_profile(self, revision_id: str) -> KnowledgeIndexProfileRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_index_profile_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        return None if row is None else _profile(row)

    def list_index_profiles(self) -> tuple[KnowledgeIndexProfileRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_index_profile_revisions
                ORDER BY published_at DESC,id DESC"""
            ).fetchall()
        return tuple(_profile(row) for row in rows)

    def create_qualification(
        self, qualification: EmbeddingQualificationSnapshot
    ) -> EmbeddingQualificationSnapshot:
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM embedding_qualification_snapshots
                WHERE qualification_sha256=?""",
                (qualification.qualification_sha256,),
            ).fetchone()
            if existing is not None:
                return _qualification(existing)
            connection.execute(
                """INSERT INTO embedding_qualification_snapshots(
                id,provider_kind,model_name,model_digest,dimension,adapter_revision,
                tokenizer_contract,vector_normalization,distance_metric,sqlite_vec_version,
                vector_index_adapter_revision,qualification_sha256,status,qualified_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    qualification.id,
                    qualification.provider_kind,
                    qualification.model_name,
                    qualification.model_digest,
                    qualification.dimension,
                    qualification.adapter_revision,
                    qualification.tokenizer_contract,
                    qualification.vector_normalization,
                    qualification.distance_metric,
                    qualification.sqlite_vec_version,
                    qualification.vector_index_adapter_revision,
                    qualification.qualification_sha256,
                    qualification.status,
                    qualification.qualified_at.isoformat(),
                ),
            )
        return qualification

    def get_qualification(self, qualification_id: str) -> EmbeddingQualificationSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM embedding_qualification_snapshots WHERE id=?",
                (qualification_id,),
            ).fetchone()
        return None if row is None else _qualification(row)

    def list_qualifications(self) -> tuple[EmbeddingQualificationSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM embedding_qualification_snapshots
                ORDER BY qualified_at DESC,id DESC"""
            ).fetchall()
        return tuple(_qualification(row) for row in rows)

    def publish_retrieval_policy(
        self, revision: RetrievalPolicyRevision
    ) -> RetrievalPolicyRevision | None:
        config = revision.model_dump(
            mode="json",
            exclude={"policy_sha256", "published_by", "published_at"},
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO retrieval_policy_revisions(
                    id,display_name,index_profile_revision_id,config_json,policy_sha256,
                    published_by,published_at) VALUES(?,?,?,?,?,?,?)""",
                    (
                        revision.id,
                        revision.display_name,
                        revision.index_profile_revision_id,
                        _json(config),
                        revision.policy_sha256,
                        revision.published_by,
                        revision.published_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return revision

    def get_retrieval_policy(self, revision_id: str) -> RetrievalPolicyRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_policy_revisions WHERE id=?", (revision_id,)
            ).fetchone()
        return None if row is None else _retrieval_policy(row)

    def list_retrieval_policies(self) -> tuple[RetrievalPolicyRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM retrieval_policy_revisions
                ORDER BY published_at DESC,id DESC"""
            ).fetchall()
        return tuple(_retrieval_policy(row) for row in rows)

    def publish_evaluation_policy(
        self, revision: RetrievalEvaluationPolicyRevision
    ) -> RetrievalEvaluationPolicyRevision | None:
        config = revision.model_dump(
            mode="json",
            exclude={"policy_sha256", "published_by", "published_at"},
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO retrieval_evaluation_policy_revisions(
                    id,retrieval_policy_revision_id,index_profile_revision_id,
                    dataset_manifest_sha256,config_json,policy_sha256,published_by,published_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        revision.id,
                        revision.retrieval_policy_revision_id,
                        revision.index_profile_revision_id,
                        revision.dataset_manifest_sha256,
                        _json(config),
                        revision.policy_sha256,
                        revision.published_by,
                        revision.published_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return revision

    def get_evaluation_policy_for_retrieval(
        self, retrieval_policy_revision_id: str
    ) -> RetrievalEvaluationPolicyRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM retrieval_evaluation_policy_revisions
                WHERE retrieval_policy_revision_id=? ORDER BY published_at DESC,id DESC LIMIT 1""",
                (retrieval_policy_revision_id,),
            ).fetchone()
        return None if row is None else _evaluation_policy(row)

    def get_evaluation_policy(self, revision_id: str) -> RetrievalEvaluationPolicyRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_evaluation_policy_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        return None if row is None else _evaluation_policy(row)

    def list_evaluation_policies(
        self,
    ) -> tuple[RetrievalEvaluationPolicyRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM retrieval_evaluation_policy_revisions
                ORDER BY published_at DESC,id DESC"""
            ).fetchall()
        return tuple(_evaluation_policy(row) for row in rows)

    def has_evaluation_policy_for_profile(self, profile_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM retrieval_evaluation_policy_revisions
                WHERE index_profile_revision_id=? LIMIT 1""",
                (profile_id,),
            ).fetchone()
        return row is not None

    def create_index_revision(
        self, revision: KnowledgeIndexRevision
    ) -> tuple[KnowledgeIndexRevision, bool]:
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM knowledge_index_revisions WHERE provider_binding_id=?
                AND index_profile_revision_id=? AND input_manifest_sha256=?""",
                (
                    revision.provider_binding_id,
                    revision.index_profile_revision_id,
                    revision.input_manifest_sha256,
                ),
            ).fetchone()
            if existing is not None:
                return _index_revision(existing), False
            connection.execute(
                """INSERT INTO knowledge_index_revisions(
                id,provider_binding_id,index_profile_revision_id,embedding_qualification_id,
                input_manifest_sha256,status,storage_uri,storage_sha256,document_count,
                chunk_count,capacity_status,version,created_by,created_at,qualified_at,
                activated_at,error_code)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                _index_values(revision),
            )
        return revision, True

    def get_index_revision(self, revision_id: str) -> KnowledgeIndexRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?", (revision_id,)
            ).fetchone()
        return None if row is None else _index_revision(row)

    def list_index_revisions(self) -> tuple[KnowledgeIndexRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_index_revisions
                ORDER BY created_at DESC,id DESC"""
            ).fetchall()
        return tuple(_index_revision(row) for row in rows)

    def list_evaluation_reports(self) -> tuple[RetrievalEvaluationReport, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT metrics_json FROM retrieval_evaluation_runs
                ORDER BY created_at DESC,id DESC"""
            ).fetchall()
        return tuple(
            RetrievalEvaluationReport.model_validate_json(str(row["metrics_json"]))
            for row in rows
        )

    def complete_index_build(
        self,
        revision: KnowledgeIndexRevision,
        *,
        storage_uri: str,
        storage_sha256: str,
        document_count: int,
        chunk_count: int,
        capacity_status: str,
    ) -> KnowledgeIndexRevision:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE knowledge_index_revisions SET status='built',storage_uri=?,
                storage_sha256=?,document_count=?,chunk_count=?,capacity_status=?,
                error_code=NULL,version=version+1
                WHERE id=? AND version=? AND status='building'""",
                (
                    storage_uri,
                    storage_sha256,
                    document_count,
                    chunk_count,
                    capacity_status,
                    revision.id,
                    revision.version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("KNOWLEDGE_INDEX_VERSION_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?", (revision.id,)
            ).fetchone()
        assert row is not None
        return _index_revision(row)

    def fail_index_build(
        self, revision: KnowledgeIndexRevision, *, error_code: str
    ) -> KnowledgeIndexRevision:
        with self._connect() as connection:
            connection.execute(
                """UPDATE knowledge_index_revisions SET status='failed',error_code=?,
                version=version+1 WHERE id=? AND status='building'""",
                (error_code, revision.id),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?", (revision.id,)
            ).fetchone()
        assert row is not None
        return _index_revision(row)

    def record_evaluation(
        self,
        report: RetrievalEvaluationReport,
        *,
        qualified_at: datetime,
    ) -> tuple[RetrievalEvaluationReport, KnowledgeIndexRevision]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT metrics_json FROM retrieval_evaluation_runs
                WHERE evaluation_policy_revision_id=? AND index_revision_id=?""",
                (report.evaluation_policy_revision_id, report.index_revision_id),
            ).fetchone()
            if existing is not None:
                persisted_report = RetrievalEvaluationReport.model_validate_json(
                    str(existing["metrics_json"])
                )
                row = connection.execute(
                    "SELECT * FROM knowledge_index_revisions WHERE id=?",
                    (report.index_revision_id,),
                ).fetchone()
                connection.commit()
                assert row is not None
                return persisted_report, _index_revision(row)

            connection.execute(
                """INSERT INTO retrieval_evaluation_runs(
                id,evaluation_policy_revision_id,index_revision_id,dataset_manifest_sha256,
                status,metrics_json,report_uri,report_sha256,run_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    report.id,
                    report.evaluation_policy_revision_id,
                    report.index_revision_id,
                    report.dataset_manifest_sha256,
                    report.status,
                    report.model_dump_json(),
                    report.report_artifact.uri,
                    str(report.report_artifact.sha256),
                    report.run_by,
                    report.created_at.isoformat(),
                ),
            )
            if report.status == "passed":
                updated = connection.execute(
                    """UPDATE knowledge_index_revisions SET status='qualified',
                    qualified_at=?,evaluation_report_uri=?,evaluation_report_sha256=?,
                    error_code=NULL,version=version+1
                    WHERE id=? AND status='built'""",
                    (
                        qualified_at.isoformat(),
                        report.report_artifact.uri,
                        str(report.report_artifact.sha256),
                        report.index_revision_id,
                    ),
                )
            else:
                updated = connection.execute(
                    """UPDATE knowledge_index_revisions SET status='failed',
                    evaluation_report_uri=?,evaluation_report_sha256=?,
                    error_code='KNOWLEDGE_RETRIEVAL_EVALUATION_FAILED',version=version+1
                    WHERE id=? AND status='built'""",
                    (
                        report.report_artifact.uri,
                        str(report.report_artifact.sha256),
                        report.index_revision_id,
                    ),
                )
            if updated.rowcount != 1:
                connection.rollback()
                raise RuntimeError("KNOWLEDGE_INDEX_EVALUATION_STATE_CONFLICT")
            row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?",
                (report.index_revision_id,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return report, _index_revision(row)

    def has_passed_evaluation(
        self, evaluation_policy_revision_id: str, index_revision_id: str
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM retrieval_evaluation_runs
                WHERE evaluation_policy_revision_id=? AND index_revision_id=?
                AND status='passed' LIMIT 1""",
                (evaluation_policy_revision_id, index_revision_id),
            ).fetchone()
        return row is not None

    def activate_index(
        self,
        revision_id: str,
        *,
        expected_pointer_version: int | None,
        activated_at: datetime,
    ) -> KnowledgeIndexRevision:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target_row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?", (revision_id,)
            ).fetchone()
            if target_row is None:
                raise RuntimeError("KNOWLEDGE_INDEX_NOT_FOUND")
            target = _index_revision(target_row)
            if target.status not in {"qualified", "active"}:
                raise RuntimeError("KNOWLEDGE_INDEX_NOT_QUALIFIED")
            pointer = connection.execute(
                """SELECT index_revision_id,version FROM knowledge_index_active_pointers
                WHERE provider_binding_id=? AND index_profile_revision_id=?""",
                (target.provider_binding_id, target.index_profile_revision_id),
            ).fetchone()
            actual_version = None if pointer is None else int(pointer["version"])
            if actual_version != expected_pointer_version:
                raise RuntimeError("KNOWLEDGE_INDEX_POINTER_VERSION_CONFLICT")
            if pointer is not None and str(pointer["index_revision_id"]) == revision_id:
                connection.commit()
                return target
            if pointer is not None:
                connection.execute(
                    """UPDATE knowledge_index_revisions SET status='superseded',
                    version=version+1 WHERE id=? AND status='active'""",
                    (str(pointer["index_revision_id"]),),
                )
            connection.execute(
                """UPDATE knowledge_index_revisions SET status='active',activated_at=?,
                version=version+1 WHERE id=?""",
                (activated_at.isoformat(), revision_id),
            )
            next_version = 1 if actual_version is None else actual_version + 1
            connection.execute(
                """INSERT INTO knowledge_index_active_pointers(
                provider_binding_id,index_profile_revision_id,index_revision_id,version,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(provider_binding_id,index_profile_revision_id)
                DO UPDATE SET index_revision_id=excluded.index_revision_id,
                version=excluded.version,updated_at=excluded.updated_at""",
                (
                    target.provider_binding_id,
                    target.index_profile_revision_id,
                    revision_id,
                    next_version,
                    activated_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_index_revisions WHERE id=?", (revision_id,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return _index_revision(row)

    def get_active_index(
        self, provider_binding_id: str, index_profile_revision_id: str
    ) -> KnowledgeIndexRevision | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT revision.* FROM knowledge_index_active_pointers pointer
                JOIN knowledge_index_revisions revision ON revision.id=pointer.index_revision_id
                WHERE pointer.provider_binding_id=? AND pointer.index_profile_revision_id=?""",
                (provider_binding_id, index_profile_revision_id),
            ).fetchone()
        return None if row is None else _index_revision(row)

    def record_retrieval(
        self,
        receipt: KnowledgeRetrievalReceipt,
        *,
        receipt_uri: str,
        receipt_sha256: str,
        hit_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_retrieval_runs(
                id,project_id,provider_binding_id,index_revision_id,
                retrieval_policy_revision_id,query_sha256,allowed_source_set_sha256,status,
                receipt_uri,receipt_sha256,hit_count,empty_reason,requested_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt.id,
                    receipt.project_id,
                    receipt.provider_binding_id,
                    receipt.index_revision_id,
                    receipt.retrieval_policy_revision_id,
                    receipt.query_sha256,
                    receipt.allowed_source_set_sha256,
                    "succeeded",
                    receipt_uri,
                    receipt_sha256,
                    hit_count,
                    receipt.empty_reason,
                    receipt.requested_by,
                    receipt.created_at.isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile(row: sqlite3.Row) -> KnowledgeIndexProfileRevision:
    config = json.loads(str(row["config_json"]))
    return KnowledgeIndexProfileRevision.model_validate(
        {
            **config,
            "config_sha256": row["config_sha256"],
            "published_by": row["published_by"],
            "published_at": row["published_at"],
        }
    )


def _qualification(row: sqlite3.Row) -> EmbeddingQualificationSnapshot:
    return EmbeddingQualificationSnapshot.model_validate(dict(row))


def _retrieval_policy(row: sqlite3.Row) -> RetrievalPolicyRevision:
    config = json.loads(str(row["config_json"]))
    return RetrievalPolicyRevision.model_validate(
        {
            **config,
            "policy_sha256": row["policy_sha256"],
            "published_by": row["published_by"],
            "published_at": row["published_at"],
        }
    )


def _evaluation_policy(row: sqlite3.Row) -> RetrievalEvaluationPolicyRevision:
    config = json.loads(str(row["config_json"]))
    return RetrievalEvaluationPolicyRevision.model_validate(
        {
            **config,
            "policy_sha256": row["policy_sha256"],
            "published_by": row["published_by"],
            "published_at": row["published_at"],
        }
    )


def _index_values(revision: KnowledgeIndexRevision) -> tuple[object, ...]:
    return (
        revision.id,
        revision.provider_binding_id,
        revision.index_profile_revision_id,
        revision.embedding_qualification_id,
        revision.input_manifest_sha256,
        revision.status,
        revision.storage_uri,
        revision.storage_sha256,
        revision.document_count,
        revision.chunk_count,
        revision.capacity_status,
        revision.version,
        revision.created_by,
        revision.created_at.isoformat(),
        None if revision.qualified_at is None else revision.qualified_at.isoformat(),
        None if revision.activated_at is None else revision.activated_at.isoformat(),
        revision.error_code,
    )


def _index_revision(row: sqlite3.Row) -> KnowledgeIndexRevision:
    return KnowledgeIndexRevision.model_validate(dict(row))
