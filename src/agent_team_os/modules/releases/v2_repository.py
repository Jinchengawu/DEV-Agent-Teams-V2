from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...delivery import DeliveryRun
from ...shared.events import ProductEvent
from .v2_domain import (
    GitHubPRReceipt,
    ReleaseApplyAttemptV2,
    ReleaseBundleV2,
    ReleaseHealthV2,
    ReleaseManifestV2,
    RemoteApplyReceipt,
    WorkspaceCandidateV2,
)


class SQLiteExternalReleaseRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def put_candidate(self, candidate: WorkspaceCandidateV2) -> WorkspaceCandidateV2:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO workspace_candidates_v2(
                    id,delivery_id,project_id,workcell_key,workspace_binding_id,repository_uri,
                    adapter_type,base_revision,candidate_revision,diff_sha256,candidate_branch,
                    verification_sha256,review_artifact_ids_json,evidence_sha256,status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        candidate.id,
                        candidate.delivery_id,
                        candidate.project_id,
                        candidate.workcell_key,
                        candidate.workspace_binding_id,
                        candidate.repository_uri,
                        candidate.adapter_type,
                        candidate.base_revision,
                        candidate.candidate_revision,
                        candidate.diff_sha256,
                        candidate.candidate_branch,
                        candidate.verification_sha256,
                        _json(candidate.review_artifact_ids),
                        candidate.evidence_sha256,
                        candidate.status,
                        candidate.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get_candidate_for_workcell(
                    candidate.delivery_id,
                    candidate.workcell_key,
                )
                if existing != candidate:
                    raise
                return existing
        return candidate

    def get_candidate(self, candidate_id: str) -> WorkspaceCandidateV2:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_CANDIDATE_COLUMNS} FROM workspace_candidates_v2 WHERE id=?",  # noqa: S608
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _candidate(row)

    def get_candidate_for_workcell(
        self,
        delivery_id: str,
        workcell_key: str,
    ) -> WorkspaceCandidateV2:
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_CANDIDATE_COLUMNS} FROM workspace_candidates_v2
                WHERE delivery_id=? AND workcell_key=?""",  # noqa: S608
                (delivery_id, workcell_key),
            ).fetchone()
        if row is None:
            raise KeyError(f"{delivery_id}:{workcell_key}")
        return _candidate(row)

    def list_candidates(self, delivery_id: str) -> tuple[WorkspaceCandidateV2, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_CANDIDATE_COLUMNS} FROM workspace_candidates_v2
                WHERE delivery_id=? ORDER BY workcell_key""",  # noqa: S608
                (delivery_id,),
            ).fetchall()
        return tuple(_candidate(row) for row in rows)

    def put_pr(self, receipt: GitHubPRReceipt) -> GitHubPRReceipt:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO github_pr_receipts(
                candidate_id,provider,pull_request_id,url,base_branch,head_branch,
                head_candidate_sha,state,receipt_sha256,observed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
                pull_request_id=excluded.pull_request_id,url=excluded.url,
                base_branch=excluded.base_branch,head_branch=excluded.head_branch,
                head_candidate_sha=excluded.head_candidate_sha,state=excluded.state,
                receipt_sha256=excluded.receipt_sha256,observed_at=excluded.observed_at""",
                (
                    receipt.candidate_id,
                    receipt.provider,
                    receipt.pull_request_id,
                    receipt.url,
                    receipt.base_branch,
                    receipt.head_branch,
                    receipt.head_candidate_sha,
                    receipt.state,
                    receipt.receipt_sha256,
                    receipt.observed_at.isoformat(),
                ),
            )
        return receipt

    def get_pr(self, candidate_id: str) -> GitHubPRReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_PR_COLUMNS} FROM github_pr_receipts WHERE candidate_id=?",  # noqa: S608
                (candidate_id,),
            ).fetchone()
        return None if row is None else _pr(row)

    def put_bundle(self, bundle: ReleaseBundleV2) -> ReleaseBundleV2:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO release_bundles_v2(
                    delivery_id,project_id,pipeline_revision_id,release_contract_snapshot_json,
                    candidate_ids_json,bundle_sha256,status,verified_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        bundle.delivery_id,
                        bundle.project_id,
                        bundle.pipeline_revision_id,
                        _json(bundle.release_contract_snapshot),
                        _json([item.id for item in bundle.candidates]),
                        bundle.bundle_sha256,
                        bundle.status,
                        bundle.verified_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get_bundle(bundle.delivery_id)
                if existing.bundle_sha256 != bundle.bundle_sha256:
                    raise
                return existing
        return bundle

    def get_bundle(self, delivery_id: str) -> ReleaseBundleV2:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT project_id,pipeline_revision_id,release_contract_snapshot_json,
                candidate_ids_json,bundle_sha256,status,verified_at
                FROM release_bundles_v2 WHERE delivery_id=?""",
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        candidate_ids = tuple(json.loads(str(row[3])))
        return ReleaseBundleV2.model_validate(
            {
                "delivery_id": delivery_id,
                "project_id": row[0],
                "pipeline_revision_id": row[1],
                "release_contract_snapshot": json.loads(str(row[2])),
                "candidates": [self.get_candidate(str(item)) for item in candidate_ids],
                "bundle_sha256": row[4],
                "status": row[5],
                "verified_at": row[6],
            }
        )

    def get_attempt(self, delivery_id: str) -> ReleaseApplyAttemptV2 | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT {_ATTEMPT_COLUMNS} FROM release_apply_attempts_v2
                WHERE delivery_id=?""",  # noqa: S608
                (delivery_id,),
            ).fetchone()
        return None if row is None else _attempt(row)

    def put_attempt(
        self,
        attempt: ReleaseApplyAttemptV2,
        *,
        expected_version: int | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if expected_version is None:
                connection.execute(
                    """INSERT INTO release_apply_attempts_v2(
                    delivery_id,project_id,bundle_sha256,status,error_code,version,
                    created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                    _attempt_values(attempt),
                )
                return
            cursor = connection.execute(
                """UPDATE release_apply_attempts_v2 SET status=?,error_code=?,version=?,
                updated_at=? WHERE delivery_id=? AND version=?""",
                (
                    attempt.status,
                    attempt.error_code,
                    attempt.version,
                    attempt.updated_at.isoformat(),
                    attempt.delivery_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("RELEASE_ATTEMPT_VERSION_CONFLICT")

    def put_remote_receipt(self, receipt: RemoteApplyReceipt) -> RemoteApplyReceipt:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO remote_apply_receipts(
                    delivery_id,ordinal,candidate_id,workcell_key,repository_uri,
                    before_revision,candidate_revision,after_revision,recovered,
                    receipt_sha256,applied_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        receipt.delivery_id,
                        receipt.ordinal,
                        receipt.candidate_id,
                        receipt.workcell_key,
                        receipt.repository_uri,
                        receipt.before_revision,
                        receipt.candidate_revision,
                        receipt.after_revision,
                        int(receipt.recovered),
                        receipt.receipt_sha256,
                        receipt.applied_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = next(
                    (
                        item
                        for item in self.list_remote_receipts(receipt.delivery_id)
                        if item.candidate_id == receipt.candidate_id
                    ),
                    None,
                )
                if existing != receipt:
                    raise
                return existing
        return receipt

    def list_remote_receipts(self, delivery_id: str) -> tuple[RemoteApplyReceipt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {_REMOTE_RECEIPT_COLUMNS} FROM remote_apply_receipts
                WHERE delivery_id=? ORDER BY ordinal""",  # noqa: S608
                (delivery_id,),
            ).fetchall()
        return tuple(_remote_receipt(row) for row in rows)

    def activate_manifest(self, manifest: ReleaseManifestV2) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT delivery_id,manifest_sha256 FROM release_manifests_v2 WHERE project_id=?",
                (manifest.project_id,),
            ).fetchone()
            if existing is not None and existing[0] == manifest.delivery_id:
                if existing[1] != manifest.manifest_sha256:
                    raise RuntimeError("RELEASE_MANIFEST_INTEGRITY_CONFLICT")
                return
            connection.execute(
                """INSERT INTO release_manifests_v2(
                project_id,delivery_id,pipeline_revision_id,bundle_sha256,repositories_json,
                manifest_sha256,status,activated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET delivery_id=excluded.delivery_id,
                pipeline_revision_id=excluded.pipeline_revision_id,
                bundle_sha256=excluded.bundle_sha256,
                repositories_json=excluded.repositories_json,
                manifest_sha256=excluded.manifest_sha256,status=excluded.status,
                activated_at=excluded.activated_at""",
                (
                    manifest.project_id,
                    manifest.delivery_id,
                    manifest.pipeline_revision_id,
                    manifest.bundle_sha256,
                    _json([item.model_dump(mode="json") for item in manifest.repositories]),
                    manifest.manifest_sha256,
                    manifest.status,
                    manifest.activated_at.isoformat(),
                ),
            )

    def get_manifest(self, project_id: str) -> ReleaseManifestV2 | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT delivery_id,pipeline_revision_id,bundle_sha256,repositories_json,
                manifest_sha256,status,activated_at FROM release_manifests_v2
                WHERE project_id=?""",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ReleaseManifestV2.model_validate(
            {
                "project_id": project_id,
                "delivery_id": row[0],
                "pipeline_revision_id": row[1],
                "bundle_sha256": row[2],
                "repositories": json.loads(str(row[3])),
                "manifest_sha256": row[4],
                "status": row[5],
                "activated_at": row[6],
            }
        )

    def put_health(self, health: ReleaseHealthV2) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO project_release_health_v2(
                project_id,status,delivery_id,bundle_sha256,error_code,version,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                status=excluded.status,delivery_id=excluded.delivery_id,
                bundle_sha256=excluded.bundle_sha256,error_code=excluded.error_code,
                version=excluded.version,updated_at=excluded.updated_at""",
                (
                    health.project_id,
                    health.status,
                    health.delivery_id,
                    health.bundle_sha256,
                    health.error_code,
                    health.version,
                    health.updated_at.isoformat(),
                ),
            )

    def get_health(self, project_id: str) -> ReleaseHealthV2:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_HEALTH_COLUMNS} FROM project_release_health_v2 WHERE project_id=?",  # noqa: S608
                (project_id,),
            ).fetchone()
        if row is None:
            return ReleaseHealthV2(project_id=project_id, status="healthy", version=1)
        return _health(row)

    def mark_delivery_needs_attention(self, delivery_id: str, error_code: str) -> None:
        self._mark_delivery(
            delivery_id,
            status="needs_attention",
            error_code=error_code,
            release_lease=False,
        )

    def mark_delivery_release_completed(
        self,
        delivery_id: str,
        manifest_sha256: str,
    ) -> None:
        self._mark_delivery(
            delivery_id,
            status="completed",
            error_code=None,
            release_lease=True,
            release_manifest_v2_sha256=manifest_sha256,
        )

    def _mark_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        error_code: str | None,
        release_lease: bool,
        release_manifest_v2_sha256: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE id=?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(delivery_id)
            delivery = DeliveryRun.model_validate_json(str(row[0]))
            if delivery.status == status and delivery.error_code == error_code:
                return
            updated = delivery.model_copy(
                update={
                    "status": status,
                    "error_code": error_code,
                    "release_manifest_v2_sha256": (
                        delivery.release_manifest_v2_sha256
                        if release_manifest_v2_sha256 is None
                        else release_manifest_v2_sha256
                    ),
                    "version": delivery.version + 1,
                    "updated_at": now,
                }
            )
            connection.execute(
                "UPDATE deliveries SET snapshot_json=? WHERE id=?",
                (updated.model_dump_json(), delivery_id),
            )
            if release_lease:
                connection.execute(
                    "DELETE FROM project_delivery_leases WHERE delivery_id=?",
                    (delivery_id,),
                )
            _append_event(connection, updated)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


_CANDIDATE_COLUMNS = (
    "id,delivery_id,project_id,workcell_key,workspace_binding_id,repository_uri,adapter_type,"
    "base_revision,candidate_revision,diff_sha256,candidate_branch,verification_sha256,"
    "review_artifact_ids_json,evidence_sha256,status,created_at"
)
_PR_COLUMNS = (
    "candidate_id,provider,pull_request_id,url,base_branch,head_branch,head_candidate_sha,state,"
    "receipt_sha256,observed_at"
)
_ATTEMPT_COLUMNS = (
    "delivery_id,project_id,bundle_sha256,status,error_code,version,created_at,updated_at"
)
_REMOTE_RECEIPT_COLUMNS = (
    "delivery_id,ordinal,candidate_id,workcell_key,repository_uri,before_revision,"
    "candidate_revision,after_revision,recovered,receipt_sha256,applied_at"
)
_HEALTH_COLUMNS = (
    "project_id,status,delivery_id,bundle_sha256,error_code,version,updated_at"
)


def _candidate(row: tuple[object, ...]) -> WorkspaceCandidateV2:
    values = dict(zip(_CANDIDATE_COLUMNS.split(","), row, strict=True))
    values["review_artifact_ids"] = json.loads(str(values.pop("review_artifact_ids_json")))
    return WorkspaceCandidateV2.model_validate(values)


def _pr(row: tuple[object, ...]) -> GitHubPRReceipt:
    return GitHubPRReceipt.model_validate(dict(zip(_PR_COLUMNS.split(","), row, strict=True)))


def _attempt(row: tuple[object, ...]) -> ReleaseApplyAttemptV2:
    return ReleaseApplyAttemptV2.model_validate(
        dict(zip(_ATTEMPT_COLUMNS.split(","), row, strict=True))
    )


def _remote_receipt(row: tuple[object, ...]) -> RemoteApplyReceipt:
    values = dict(zip(_REMOTE_RECEIPT_COLUMNS.split(","), row, strict=True))
    values["recovered"] = bool(values["recovered"])
    return RemoteApplyReceipt.model_validate(values)


def _health(row: tuple[object, ...]) -> ReleaseHealthV2:
    return ReleaseHealthV2.model_validate(dict(zip(_HEALTH_COLUMNS.split(","), row, strict=True)))


def _attempt_values(attempt: ReleaseApplyAttemptV2) -> tuple[object, ...]:
    return (
        attempt.delivery_id,
        attempt.project_id,
        attempt.bundle_sha256,
        attempt.status,
        attempt.error_code,
        attempt.version,
        attempt.created_at.isoformat(),
        attempt.updated_at.isoformat(),
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _append_event(connection: sqlite3.Connection, delivery: DeliveryRun) -> None:
    event = ProductEvent(
        event_type=f"delivery.{delivery.status}",
        aggregate_type="delivery",
        aggregate_id=delivery.id,
        aggregate_version=delivery.version,
        project_id=delivery.project_id,
        payload={"status": delivery.status, "error_code": delivery.error_code},
    )
    connection.execute(
        """INSERT INTO product_events(
        event_id,event_type,aggregate_type,aggregate_id,aggregate_version,
        project_id,payload_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)""",
        (
            event.id,
            event.event_type,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            event.project_id,
            _json(event.payload),
            event.occurred_at.isoformat(),
        ),
    )
