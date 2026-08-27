from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...shared.hashes import sha256_json
from .domain import (
    CalibrationProfile,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationRun,
    HumanReview,
    evaluation_report_sha256,
)


class SQLiteEvaluationRepository:
    def __init__(self, database: Path) -> None:
        self.database = database

    def create_run(self, run: EvaluationRun) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evaluation_runs(
                id,suite_json,candidate_json,baseline_json,mode,profile,seed,concurrency_json,
                timeout_seconds,max_cost_usd,status,version,evidence_identity,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.id,
                    _json(run.suite.model_dump(mode="json")),
                    _json(run.candidate.model_dump(mode="json")),
                    _json(run.baseline.model_dump(mode="json")),
                    run.mode,
                    run.profile,
                    run.seed,
                    _json(run.concurrency),
                    run.timeout_seconds,
                    run.max_cost_usd,
                    run.status,
                    run.version,
                    run.evidence_identity,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def get_run(self, run_id: str) -> EvaluationRun:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id,suite_json,candidate_json,baseline_json,mode,profile,seed,
                concurrency_json,timeout_seconds,max_cost_usd,status,version,evidence_identity,
                created_at,updated_at FROM evaluation_runs WHERE id=?""",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        keys = (
            "id",
            "suite",
            "candidate",
            "baseline",
            "mode",
            "profile",
            "seed",
            "concurrency",
            "timeout_seconds",
            "max_cost_usd",
            "status",
            "version",
            "evidence_identity",
            "created_at",
            "updated_at",
        )
        values = dict(zip(keys, row, strict=True))
        for field in ("suite", "candidate", "baseline", "concurrency"):
            values[field] = json.loads(str(values[field]))
        return EvaluationRun.model_validate(values)

    def compare_and_swap_run(self, expected_version: int, run: EvaluationRun) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_runs SET status=?,version=?,updated_at=?
                WHERE id=? AND version=?""",
                (run.status, run.version, run.updated_at.isoformat(), run.id, expected_version),
            )
        return cursor.rowcount == 1

    def append_cases(self, cases: tuple[EvaluationCaseResult, ...]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO evaluation_case_results(
                id,run_id,case_id,dimension,category,difficulty,status,candidate_score,
                baseline_score,metrics_json,judgment_json,artifact_sha256,trace_sha256,
                failure_code,evidence_identity,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        item.id,
                        item.run_id,
                        item.case_id,
                        item.dimension.value,
                        item.category,
                        item.difficulty,
                        item.status,
                        item.candidate_score,
                        item.baseline_score,
                        _json([metric.model_dump(mode="json") for metric in item.metrics]),
                        _json(item.judgment.model_dump(mode="json")) if item.judgment else None,
                        item.artifact_sha256,
                        item.trace_sha256,
                        item.failure_code,
                        item.evidence_identity,
                        item.created_at.isoformat(),
                    )
                    for item in cases
                ],
            )

    def list_cases(self, run_id: str) -> tuple[EvaluationCaseResult, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,run_id,case_id,dimension,category,difficulty,status,
                candidate_score,baseline_score,metrics_json,judgment_json,artifact_sha256,
                trace_sha256,failure_code,evidence_identity,created_at
                FROM evaluation_case_results WHERE run_id=? ORDER BY created_at,id""",
                (run_id,),
            ).fetchall()
        fields = (
            "id",
            "run_id",
            "case_id",
            "dimension",
            "category",
            "difficulty",
            "status",
            "candidate_score",
            "baseline_score",
            "metrics",
            "judgment",
            "artifact_sha256",
            "trace_sha256",
            "failure_code",
            "evidence_identity",
            "created_at",
        )
        results = []
        for row in rows:
            values = dict(zip(fields, row, strict=True))
            values["metrics"] = json.loads(str(values["metrics"]))
            if values["judgment"] is not None:
                values["judgment"] = json.loads(str(values["judgment"]))
            results.append(EvaluationCaseResult.model_validate(values))
        return tuple(results)

    def save_report(self, report: EvaluationReport) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evaluation_reports(run_id,report_json,evidence_sha256,created_at)
                VALUES(?,?,?,?)""",
                (
                    report.run_id,
                    report.model_dump_json(),
                    report.evidence_sha256,
                    report.created_at.isoformat(),
                ),
            )

    def get_report(self, run_id: str) -> EvaluationReport:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM evaluation_reports WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        report = EvaluationReport.model_validate_json(str(row[0]))
        if report.evidence_sha256 != evaluation_report_sha256(report):
            raise ValueError("EVALUATION_REPORT_HASH_INVALID")
        return report

    def calibration_reports(
        self, *, suite_sha256: str, subject_fingerprint: str
    ) -> tuple[EvaluationReport, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT reports.run_id,runs.suite_json,runs.candidate_json,
                runs.baseline_json
                FROM evaluation_reports reports
                JOIN evaluation_runs runs ON runs.id=reports.run_id
                WHERE runs.mode='offline' AND runs.profile='standard'
                AND runs.status IN ('running','completed') ORDER BY reports.created_at"""
            ).fetchall()
        matched: list[EvaluationReport] = []
        for row in rows:
            suite = json.loads(str(row[1]))
            candidate = json.loads(str(row[2]))
            baseline = json.loads(str(row[3]))
            if (
                suite.get("source_sha256") != suite_sha256
                or candidate.get("pipeline_fingerprint") != subject_fingerprint
                or candidate != baseline
            ):
                continue
            matched.append(self.get_report(str(row[0])))
        return tuple(matched)

    def save_calibration(self, profile: CalibrationProfile) -> CalibrationProfile:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_calibrations(
                id,suite_sha256,subject_fingerprint,sample_count,metric_medians_json,
                metric_mad_json,evidence_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    profile.id,
                    profile.suite_sha256,
                    profile.subject_fingerprint,
                    profile.sample_count,
                    _json(profile.metric_medians),
                    _json(profile.metric_mad),
                    profile.evidence_sha256,
                    profile.created_at.isoformat(),
                ),
            )
        return self.get_calibration(
            suite_sha256=str(profile.suite_sha256),
            subject_fingerprint=str(profile.subject_fingerprint),
        )

    def get_calibration(self, *, suite_sha256: str, subject_fingerprint: str) -> CalibrationProfile:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id,suite_sha256,subject_fingerprint,sample_count,
                metric_medians_json,metric_mad_json,evidence_sha256,created_at
                FROM evaluation_calibrations
                WHERE suite_sha256=? AND subject_fingerprint=?""",
                (suite_sha256, subject_fingerprint),
            ).fetchone()
        if row is None:
            raise KeyError(subject_fingerprint)
        values = dict(
            zip(
                (
                    "id",
                    "suite_sha256",
                    "subject_fingerprint",
                    "sample_count",
                    "metric_medians",
                    "metric_mad",
                    "evidence_sha256",
                    "created_at",
                ),
                row,
                strict=True,
            )
        )
        values["metric_medians"] = json.loads(str(values["metric_medians"]))
        values["metric_mad"] = json.loads(str(values["metric_mad"]))
        profile = CalibrationProfile.model_validate(values)
        payload = {
            "suite_sha256": str(profile.suite_sha256),
            "subject_fingerprint": str(profile.subject_fingerprint),
            "sample_count": profile.sample_count,
            "metric_medians": profile.metric_medians,
            "metric_mad": profile.metric_mad,
        }
        if profile.evidence_sha256 != sha256_json(payload):
            raise ValueError("EVALUATION_CALIBRATION_HASH_INVALID")
        return profile

    def append_reviews(self, reviews: tuple[HumanReview, ...]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO evaluation_human_reviews(
                id,run_id,case_id,reviewer_id,outcome,notes_sha256,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        item.id,
                        item.run_id,
                        item.case_id,
                        item.reviewer_id,
                        item.outcome,
                        item.notes_sha256,
                        item.created_at.isoformat(),
                    )
                    for item in reviews
                ],
            )

    def list_reviews(self, run_id: str) -> tuple[HumanReview, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,run_id,case_id,reviewer_id,outcome,notes_sha256,created_at
                FROM evaluation_human_reviews WHERE run_id=? ORDER BY created_at,id""",
                (run_id,),
            ).fetchall()
        return tuple(
            HumanReview.model_validate(
                dict(
                    zip(
                        (
                            "id",
                            "run_id",
                            "case_id",
                            "reviewer_id",
                            "outcome",
                            "notes_sha256",
                            "created_at",
                        ),
                        row,
                        strict=True,
                    )
                )
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
