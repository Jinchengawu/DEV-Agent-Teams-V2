from __future__ import annotations

import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ...infrastructure.acwm import ACWMPipelineGraphRuntime
from ...infrastructure.database import MigrationRunner
from ...shared.errors import ProductError
from ...shared.hashes import Sha256, sha256_json
from ..evidence import EvidenceLedger
from ..orchestration import (
    PipelineCatalog,
    PipelineRevision,
    PipelineRunLedger,
    SQLitePipelineRunRepository,
)
from .dataset import EvaluationDataset, default_dataset_dir, load_evaluation_dataset
from .domain import (
    CalibrationProfile,
    DimensionSummary,
    EvaluationCaseResult,
    EvaluationDimension,
    EvaluationReport,
    EvaluationRun,
    EvaluationRunRequest,
    EvaluationSuite,
    HumanReview,
    Judgment,
    MetricObservation,
    SubjectSnapshot,
    evaluation_report_sha256,
)
from .repository import SQLiteEvaluationRepository
from .scoring import (
    ast_match,
    pairwise_rates,
    percentile,
    quasi_exact_match,
    wilson_interval,
)

_PROFILE = {
    "smoke": (10, (1,)),
    "standard": (100, (1, 4, 8)),
    "live": (10, (2,)),
}

def builtin_suite(project_root: Path | None = None) -> EvaluationSuite:
    root = project_root or Path(__file__).parents[4]
    return load_evaluation_dataset(default_dataset_dir(root)).suite()


class EvaluationService:
    def __init__(
        self,
        repository: SQLiteEvaluationRepository,
        catalog: PipelineCatalog,
        *,
        report_dir: Path,
        project_root: Path,
        evidence: EvidenceLedger | None = None,
        dataset_dir: Path | None = None,
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.report_dir = report_dir
        self.project_root = project_root
        self.evidence = evidence
        self.dataset: EvaluationDataset = load_evaluation_dataset(
            dataset_dir or default_dataset_dir(project_root)
        )

    def create(self, request: EvaluationRunRequest) -> EvaluationRun:
        if request.suite_id != "agent-team-os-mvp":
            raise ProductError(
                code="EVALUATION_SUITE_NOT_FOUND",
                title="评测套件不存在",
                detail=f"没有找到固定评测套件 {request.suite_id}。",
                repair="选择已安装且已校验哈希的评测套件。",
                status_code=404,
            )
        pipeline = self.catalog.repository.get_pipeline(request.pipeline_id)
        if pipeline.active_revision is None:
            raise ProductError(
                code="EVALUATION_BASELINE_NOT_ACTIVE",
                title="没有可比较的激活版本",
                detail="目标 Pipeline 尚未激活任何已发布版本。",
                repair="先发布并激活一个 Pipeline Revision。",
            )
        baseline_revision = self.catalog.get_revision(request.pipeline_id, pipeline.active_revision)
        candidate_revision = self.catalog.get_revision(
            request.pipeline_id, request.candidate_revision or pipeline.active_revision
        )
        count, concurrency = _PROFILE[request.profile]
        del count
        evidence_identity = (
            "deterministic-evaluation-fixture"
            if request.mode == "offline"
            else "live-runtime-unconfigured"
        )
        run = EvaluationRun(
            suite=self.dataset.suite(),
            candidate=self._snapshot(candidate_revision),
            baseline=self._snapshot(baseline_revision),
            mode=request.mode,
            profile=request.profile,
            seed=request.seed,
            concurrency=concurrency,
            timeout_seconds=request.timeout_seconds,
            max_cost_usd=request.max_cost_usd,
            evidence_identity=evidence_identity,
        )
        self.repository.create_run(run)
        return run

    def execute(self, run_id: str) -> EvaluationRun:
        run = self.get(run_id)
        if run.status in {"completed", "blocked", "cancelled"}:
            return run
        running = self._transition(run, "running")
        try:
            cases = self._run_cases(running)
            if self.get(run_id).status == "cancelled":
                return self.get(run_id)
            self.repository.append_cases(cases)
            report = self._build_report(running, cases)
            self.repository.save_report(report)
            self._maybe_calibrate(running)
            self._write_report(report)
            if self.evidence is not None:
                self.evidence.record_evaluation_report(report.model_dump(mode="json"))
            return self._transition(
                running, "blocked" if report.gate_status == "not_run" else "completed"
            )
        except Exception:
            current = self.get(run_id)
            if current.status == "running":
                self._transition(current, "failed")
            raise

    def start(self, request: EvaluationRunRequest) -> EvaluationRun:
        return self.execute(self.create(request).id)

    def get(self, run_id: str) -> EvaluationRun:
        try:
            return self.repository.get_run(run_id)
        except KeyError as error:
            raise ProductError(
                code="EVALUATION_RUN_NOT_FOUND",
                title="评测运行不存在",
                detail=f"没有找到评测运行 {run_id}。",
                repair="刷新评测运行列表后重试。",
                status_code=404,
            ) from error

    def cases(self, run_id: str) -> tuple[EvaluationCaseResult, ...]:
        self.get(run_id)
        return self.repository.list_cases(run_id)

    def report(self, run_id: str) -> EvaluationReport:
        self.get(run_id)
        try:
            return self.repository.get_report(run_id)
        except KeyError as error:
            raise ProductError(
                code="EVALUATION_REPORT_NOT_READY",
                title="评测报告尚未生成",
                detail="评测运行尚未完成，或已经失败。",
                repair="等待运行完成后重新读取报告。",
                status_code=409,
            ) from error
        except ValueError as error:
            raise ProductError(
                code="EVALUATION_REPORT_HASH_INVALID",
                title="评测报告完整性校验失败",
                detail="报告内容与不可变证据哈希不一致。",
                repair="保留数据库并重新运行评测，不要使用该报告作为门禁证据。",
                status_code=409,
            ) from error

    def cancel(self, run_id: str) -> EvaluationRun:
        current = self.get(run_id)
        if current.status in {"completed", "failed", "blocked"}:
            raise ProductError(
                code="EVALUATION_RUN_TERMINAL",
                title="评测运行已经结束",
                detail="终态评测不能取消。",
                repair="创建新的评测运行。",
            )
        if current.status == "cancelled":
            return current
        return self._transition(current, "cancelled")

    def import_reviews(
        self, run_id: str, reviews: tuple[HumanReview, ...]
    ) -> tuple[HumanReview, ...]:
        cases = {item.case_id for item in self.cases(run_id)}
        for review in reviews:
            if review.run_id != run_id or review.case_id not in cases:
                raise ProductError(
                    code="HUMAN_REVIEW_CASE_INVALID",
                    title="人工复核不属于该评测",
                    detail="复核中的 run_id 或 case_id 与目标评测不一致。",
                    repair="使用 human-export 产生的盲化样本填写复核。",
                )
        self.repository.append_reviews(reviews)
        return self.repository.list_reviews(run_id)

    def human_review_sample(self, run_id: str) -> tuple[EvaluationCaseResult, ...]:
        run = self.get(run_id)
        cases = self.cases(run_id)
        required = {
            item.case_id
            for item in cases
            if item.status in {"failed", "blocked"}
            or (item.judgment is not None and item.judgment.outcome == "conflict")
        }
        target = min(50, max(10, round(len(cases) * 0.2)))
        remainder = [item for item in cases if item.case_id not in required]
        random.Random(run.seed).shuffle(remainder)
        selected = required | {item.case_id for item in remainder[: max(0, target - len(required))]}
        return tuple(item for item in cases if item.case_id in selected)

    def _transition(self, current: EvaluationRun, status: str) -> EvaluationRun:
        updated = current.model_copy(
            update={
                "status": status,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not self.repository.compare_and_swap_run(current.version, updated):
            latest = self.repository.get_run(current.id)
            raise ProductError(
                code="EVALUATION_RUN_VERSION_CONFLICT",
                title="评测运行版本冲突",
                detail="评测运行已经被另一个执行器更新。",
                repair="刷新运行状态后重试。",
                expected_version=current.version,
                actual_version=latest.version,
            )
        return updated

    def _run_cases(self, run: EvaluationRun) -> tuple[EvaluationCaseResult, ...]:
        count = _PROFILE[run.profile][0]
        rng = random.Random(run.seed)
        base_cases = self.dataset.execution_cases(run.mode)
        expanded = [dict(base_cases[index % len(base_cases)]) for index in range(count)]
        rng.shuffle(expanded)
        control_database = self.report_dir / "work" / f"{run.id}.sqlite"
        MigrationRunner(control_database, self.project_root / "migrations").migrate()
        control_runs = PipelineRunLedger(
            SQLitePipelineRunRepository(control_database), ACWMPipelineGraphRuntime()
        )
        measured: list[EvaluationCaseResult] = []
        repetitions = range(3) if run.profile == "standard" else range(1)
        with TestClient(self._http_probe_app()) as http_probe:
            for concurrency in run.concurrency:
                for repetition in repetitions:
                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futures = tuple(
                            executor.submit(
                                self._score_case,
                                run,
                                index,
                                case,
                                control_runs,
                                http_probe,
                                concurrency=concurrency,
                                repetition=repetition,
                            )
                            for index, case in enumerate(expanded)
                        )
                        batch = tuple(future.result() for future in futures)
                    if run.profile != "standard" or repetition > 0:
                        measured.extend(batch)
        return tuple(measured)

    def _score_case(
        self,
        run: EvaluationRun,
        index: int,
        case: dict[str, object],
        control_runs: PipelineRunLedger,
        http_probe: TestClient,
        *,
        concurrency: int,
        repetition: int,
    ) -> EvaluationCaseResult:
        started = perf_counter()
        dimension = EvaluationDimension(str(case["dimension"]))
        category = str(case["category"])
        status: str = "passed"
        score: float | None = 1.0
        failure_code = None
        judgment = None
        trace_hash = None
        metrics: list[MetricObservation] = []
        if run.mode == "live":
            status = "blocked"
            score = None
            failure_code = "LIVE_EVALUATION_RUNTIME_NOT_CONFIGURED"
        elif dimension == EvaluationDimension.TOOL_CALL:
            raw_actual = case.get("actual_calls", ())
            raw_expected = case.get("expected_calls", ())
            actual = (
                tuple(item for item in raw_actual if isinstance(item, dict))
                if isinstance(raw_actual, (list, tuple))
                else ()
            )
            expected = (
                tuple(item for item in raw_expected if isinstance(item, dict))
                if isinstance(raw_expected, (list, tuple))
                else ()
            )
            matched = ast_match(actual, expected, parallel=bool(case.get("parallel", False)))
            score = 1.0 if matched else 0.0
            status = "passed" if matched else "failed"
            trace_hash = sha256_json({"actual": actual, "expected": expected})
        elif dimension == EvaluationDimension.GENERAL_AGENT:
            matched = quasi_exact_match(
                case.get("actual"), case.get("expected"), str(case.get("expected_type", "text"))
            )
            score = 1.0 if matched else 0.0
            status = "passed" if matched else "failed"
        elif dimension == EvaluationDimension.DATA_GENERATION:
            outcome: Literal["tie", "conflict"] = (
                "tie" if run.candidate == run.baseline else "conflict"
            )
            judgment = Judgment(
                judge_identity="deterministic-evaluation-fixture",
                rubric_version="full-chain-v1",
                outcome=outcome,
                rationale_sha256=sha256_json(
                    {"candidate": run.candidate, "baseline": run.baseline, "outcome": outcome}
                ),
            )
            if outcome == "conflict":
                status = "blocked"
                score = None
                failure_code = "INDEPENDENT_LLM_JUDGE_REQUIRED"
        else:
            candidate_first = bool(
                random.Random(f"{run.seed}:{concurrency}:{repetition}:{index}").getrandbits(1)
            )
            order: tuple[tuple[str, str], ...] = (
                ("candidate", run.candidate.pipeline_revision_id),
                ("baseline", run.baseline.pipeline_revision_id),
            )
            if not candidate_first:
                order = tuple(reversed(order))
            observations: dict[str, tuple[float, tuple[MetricObservation, ...], float]] = {}
            for label, reference in order:
                graph_latency, graph_metrics = self._graph_probe(
                    control_runs,
                    self._revision(reference),
                    (f"evaluation:{run.id}:{label}:{concurrency}:{repetition}:{index}"),
                )
                observations[label] = (
                    graph_latency,
                    graph_metrics,
                    self._http_probe(http_probe, reference),
                )
            candidate_latency, candidate_metrics, candidate_http = observations["candidate"]
            baseline_latency, baseline_metrics, baseline_http = observations["baseline"]
            metrics.extend(
                (
                    MetricObservation(
                        name="candidate_graph_total_latency",
                        value=candidate_latency,
                        unit="ms",
                    ),
                    MetricObservation(
                        name="baseline_graph_total_latency",
                        value=baseline_latency,
                        unit="ms",
                    ),
                    MetricObservation(
                        name="candidate_http_latency", value=candidate_http, unit="ms"
                    ),
                    MetricObservation(name="baseline_http_latency", value=baseline_http, unit="ms"),
                    *candidate_metrics,
                    *baseline_metrics,
                )
            )
        elapsed_ms = (perf_counter() - started) * 1000
        artifact = {
            "case": case,
            "candidate": run.candidate.pipeline_fingerprint,
            "baseline": run.baseline.pipeline_fingerprint,
            "status": status,
            "score": score,
        }
        difficulty_value = case.get("difficulty")
        difficulty = difficulty_value if isinstance(difficulty_value, int) else None
        dataset_case_id = str(case.get("dataset_case_id", f"{dimension.value}.{category}"))
        return EvaluationCaseResult(
            run_id=run.id,
            case_id=(f"{dataset_case_id}:c{concurrency}:r{repetition}:{index + 1:04d}"),
            dimension=dimension,
            category=category,
            difficulty=difficulty,
            status=status,  # type: ignore[arg-type]
            candidate_score=score,
            baseline_score=score,
            metrics=tuple(metrics)
            + (
                MetricObservation(name="configured_concurrency", value=concurrency, unit="workers"),
                MetricObservation(name="repetition", value=repetition, unit="index"),
                MetricObservation(name="evaluation_dispatch_latency", value=elapsed_ms, unit="ms"),
            ),
            judgment=judgment,
            artifact_sha256=sha256_json(artifact),
            trace_sha256=trace_hash,
            failure_code=failure_code,
            evidence_identity=run.evidence_identity,
        )

    def _build_report(
        self, run: EvaluationRun, cases: tuple[EvaluationCaseResult, ...]
    ) -> EvaluationReport:
        summaries = tuple(
            self._summary(dimension, cases, same_subject=run.candidate == run.baseline)
            for dimension in EvaluationDimension
        )
        reasons: list[str] = []
        try:
            self.repository.get_calibration(
                suite_sha256=str(run.suite.source_sha256),
                subject_fingerprint=str(run.baseline.pipeline_fingerprint),
            )
        except KeyError:
            gate_status: str = "calibrating"
            calibrated = False
        else:
            gate_status = "passed"
            calibrated = True
        if run.mode == "live":
            gate_status = "not_run"
            reasons.append("LIVE_EVALUATION_RUNTIME_NOT_CONFIGURED")
        if any(item.status == "failed" for item in cases):
            gate_status = "failed"
            reasons.append("AUTOMATIC_CORRECTNESS_REGRESSION")
        if (
            any(
                item.delta_percentage_points is not None and item.delta_percentage_points < -2
                for item in summaries
            )
            and calibrated
        ):
            gate_status = "failed"
            reasons.append("ACCURACY_REGRESSION_GT_2PP")
        if (
            any(
                item.latency_regression_percent is not None and item.latency_regression_percent > 20
                for item in summaries
            )
            and calibrated
        ):
            gate_status = "failed"
            reasons.append("P95_LATENCY_REGRESSION_GT_20_PERCENT")
        if any(
            item.dimension == EvaluationDimension.CONTROL_PLANE
            and item.status in {"blocked", "unsupported"}
            for item in cases
        ):
            reasons.append("CONTROL_PLANE_PROBE_NOT_CONFIGURED")
        if any(item.failure_code == "INDEPENDENT_LLM_JUDGE_REQUIRED" for item in cases):
            gate_status = "blocked"
            reasons.append("INDEPENDENT_LLM_JUDGE_REQUIRED")
        created_at = datetime.now(UTC)
        payload = {
            "run_id": run.id,
            "suite_id": run.suite.id,
            "suite_version": run.suite.version,
            "mode": run.mode,
            "profile": run.profile,
            "candidate": run.candidate.model_dump(mode="json"),
            "baseline": run.baseline.model_dump(mode="json"),
            "dimensions": [item.model_dump(mode="json") for item in summaries],
            "gate_status": gate_status,
            "gate_reasons": tuple(dict.fromkeys(reasons)),
            "proof_scope": "fixture_harness_only" if run.mode == "offline" else "live_runtime",
            "official_benchmark": False,
            "human_review_required": any(
                item.judgment and item.judgment.outcome == "conflict" for item in cases
            ),
            "human_agreement": None,
            "cohens_kappa": None,
            "created_at": created_at,
        }
        draft = EvaluationReport(
            **payload,  # type: ignore[arg-type]
            evidence_sha256=sha256_json(payload),
        )
        return draft.model_copy(update={"evidence_sha256": evaluation_report_sha256(draft)})

    def _maybe_calibrate(self, run: EvaluationRun) -> CalibrationProfile | None:
        if run.mode != "offline" or run.profile != "standard" or run.candidate != run.baseline:
            return None
        try:
            return self.repository.get_calibration(
                suite_sha256=str(run.suite.source_sha256),
                subject_fingerprint=str(run.candidate.pipeline_fingerprint),
            )
        except KeyError:
            pass
        reports = self.repository.calibration_reports(
            suite_sha256=str(run.suite.source_sha256),
            subject_fingerprint=str(run.candidate.pipeline_fingerprint),
        )
        if len(reports) < 3:
            return None
        series: dict[str, list[float]] = {}
        for report in reports[:3]:
            for summary in report.dimensions:
                if summary.candidate_accuracy is not None:
                    series.setdefault(f"{summary.dimension.value}.accuracy", []).append(
                        summary.candidate_accuracy
                    )
                if summary.p95_latency_ms is not None:
                    series.setdefault(f"{summary.dimension.value}.p95_latency_ms", []).append(
                        summary.p95_latency_ms
                    )
        medians = {name: median(values) for name, values in series.items()}
        deviations = {
            name: median([abs(value - medians[name]) for value in values])
            for name, values in series.items()
        }
        payload = {
            "suite_sha256": str(run.suite.source_sha256),
            "subject_fingerprint": str(run.candidate.pipeline_fingerprint),
            "sample_count": 3,
            "metric_medians": medians,
            "metric_mad": deviations,
        }
        return self.repository.save_calibration(
            CalibrationProfile(
                suite_sha256=run.suite.source_sha256,
                subject_fingerprint=run.candidate.pipeline_fingerprint,
                sample_count=3,
                metric_medians=medians,
                metric_mad=deviations,
                evidence_sha256=sha256_json(payload),
            )
        )

    def _summary(
        self,
        dimension: EvaluationDimension,
        cases: tuple[EvaluationCaseResult, ...],
        *,
        same_subject: bool,
    ) -> DimensionSummary:
        selected = tuple(item for item in cases if item.dimension == dimension)
        evaluated = tuple(item for item in selected if item.candidate_score is not None)
        candidate = tuple(
            item.candidate_score for item in evaluated if item.candidate_score is not None
        )
        baseline = tuple(
            item.baseline_score for item in evaluated if item.baseline_score is not None
        )
        outcomes = tuple(
            item.judgment.outcome
            for item in selected
            if item.judgment is not None and item.judgment.outcome != "conflict"
        )
        rates = pairwise_rates(outcomes)
        dispatch_latencies = tuple(
            metric.value
            for item in selected
            for metric in item.metrics
            if metric.name == "evaluation_dispatch_latency"
        )
        candidate_graph_latencies = tuple(
            metric.value
            for item in selected
            for metric in item.metrics
            if metric.name == "candidate_graph_total_latency"
        )
        baseline_graph_latencies = tuple(
            metric.value
            for item in selected
            for metric in item.metrics
            if metric.name == "baseline_graph_total_latency"
        )
        candidate_latencies = candidate_graph_latencies or dispatch_latencies
        baseline_latencies = baseline_graph_latencies or dispatch_latencies
        candidate_p95 = percentile(candidate_latencies, 95)
        baseline_p95 = percentile(baseline_latencies, 95)
        latency_regression = (
            0.0
            if same_subject
            else ((candidate_p95 - baseline_p95) / baseline_p95) * 100
            if candidate_p95 is not None and baseline_p95 is not None and baseline_p95 > 0
            else None
        )
        candidate_accuracy = sum(candidate) / len(candidate) if candidate else None
        baseline_accuracy = sum(baseline) / len(baseline) if baseline else None
        delta = (
            (candidate_accuracy - baseline_accuracy) * 100
            if candidate_accuracy is not None and baseline_accuracy is not None
            else None
        )
        metric_names = {
            metric.name for item in selected for metric in item.metrics if metric.unit == "ms"
        }
        metric_percentiles: dict[str, dict[str, float]] = {}
        for name in sorted(metric_names):
            values = tuple(
                metric.value for item in selected for metric in item.metrics if metric.name == name
            )
            p50 = percentile(values, 50)
            p95 = percentile(values, 95)
            p99 = percentile(values, 99)
            if p50 is not None and p95 is not None and p99 is not None:
                metric_percentiles[name] = {"p50": p50, "p95": p95, "p99": p99}
        return DimensionSummary(
            dimension=dimension,
            total=len(selected),
            evaluated=len(evaluated),
            passed=sum(item.status == "passed" for item in selected),
            failed=sum(item.status == "failed" for item in selected),
            blocked=sum(item.status == "blocked" for item in selected),
            unsupported=sum(item.status == "unsupported" for item in selected),
            candidate_accuracy=candidate_accuracy,
            candidate_accuracy_ci95=wilson_interval(
                sum(value == 1.0 for value in candidate), len(candidate)
            ),
            baseline_accuracy=baseline_accuracy,
            delta_percentage_points=delta,
            wins=_count(rates["wins"]),
            ties=_count(rates["ties"]),
            losses=_count(rates["losses"]),
            win_rate=rates["win_rate"] if isinstance(rates["win_rate"], float) else None,
            non_loss_rate=rates["non_loss_rate"]
            if isinstance(rates["non_loss_rate"], float)
            else None,
            p50_latency_ms=percentile(candidate_latencies, 50),
            p95_latency_ms=candidate_p95,
            p99_latency_ms=percentile(candidate_latencies, 99),
            baseline_p95_latency_ms=baseline_p95,
            latency_regression_percent=latency_regression,
            metric_percentiles=metric_percentiles,
        )

    def _write_report(self, report: EvaluationReport) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        stem = self.report_dir / report.run_id
        (stem.with_suffix(".json")).write_text(report.model_dump_json(indent=2), encoding="utf-8")
        rows = "\n".join(
            f"| {item.dimension.value} | {item.evaluated}/{item.total} | "
            f"{item.passed} | {item.failed} | {item.blocked + item.unsupported} |"
            for item in report.dimensions
        )
        markdown = (
            f"# 评测报告 {report.run_id}\n\n"
            f"- 门禁状态：`{report.gate_status}`\n"
            f"- 证明范围：`{report.proof_scope}`\n"
            f"- 官方 Benchmark：`{report.official_benchmark}`\n"
            f"- Evidence SHA-256: `{report.evidence_sha256}`\n\n"
            "| 评测维度 | 已评测/总数 | 通过 | 失败 | 阻塞 |\n"
            "|---|---:|---:|---:|---:|\n"
            f"{rows}\n"
        )
        stem.with_suffix(".md").write_text(markdown, encoding="utf-8")

    def _revision(self, reference: str) -> PipelineRevision:
        pipeline_id, raw_revision = reference.rsplit(":", 1)
        return self.catalog.get_revision(pipeline_id, int(raw_revision))

    def _http_probe_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/pipeline-revisions/{pipeline_id}/{revision}")
        def get_revision(pipeline_id: str, revision: int) -> dict[str, object]:
            found = self.catalog.get_revision(pipeline_id, revision)
            return {
                "pipeline_revision_id": f"{found.pipeline_id}:{found.revision}",
                "fingerprint": found.fingerprint,
            }

        return app

    @staticmethod
    def _http_probe(client: TestClient, reference: str) -> float:
        pipeline_id, raw_revision = reference.rsplit(":", 1)
        started = perf_counter()
        response = client.get(f"/pipeline-revisions/{pipeline_id}/{raw_revision}")
        latency_ms = (perf_counter() - started) * 1000
        response.raise_for_status()
        if response.json().get("pipeline_revision_id") != reference:
            raise RuntimeError("HTTP Pipeline snapshot probe returned the wrong revision")
        return latency_ms

    @staticmethod
    def _graph_probe(
        runs: PipelineRunLedger,
        revision: PipelineRevision,
        delivery_id: str,
    ) -> tuple[float, tuple[MetricObservation, ...]]:
        started = perf_counter()
        graph_run = runs.start(delivery_id=delivery_id, revision=revision)
        start_ms = (perf_counter() - started) * 1000
        cancel_mark = perf_counter()
        cancelled = runs.transition(
            graph_run.id,
            command="cancel",
            node_id="",
            expected_version=graph_run.version,
        )
        cancel_ms = (perf_counter() - cancel_mark) * 1000
        recovery_mark = perf_counter()
        recovered = runs.get(graph_run.id)
        recovery_ms = (perf_counter() - recovery_mark) * 1000
        if cancelled.status != "cancelled" or recovered != cancelled:
            raise RuntimeError("GraphRun cancellation recovery evidence is inconsistent")
        return (
            start_ms + cancel_ms + recovery_ms,
            (
                MetricObservation(name="graph_start_latency", value=start_ms, unit="ms"),
                MetricObservation(name="graph_cas_cancel_latency", value=cancel_ms, unit="ms"),
                MetricObservation(name="graph_recovery_read_latency", value=recovery_ms, unit="ms"),
            ),
        )

    def _snapshot(self, revision: PipelineRevision) -> SubjectSnapshot:
        return SubjectSnapshot(
            pipeline_revision_id=f"{revision.pipeline_id}:{revision.revision}",
            pipeline_fingerprint=Sha256.validate(revision.fingerprint),
            binding_model=revision.binding_model,
            deployment_snapshots=revision.resolved_provider_bindings,
            git_revision=_git_revision(self.project_root),
            acwm_revision=_acwm_revision(self.project_root),
        )


def _git_revision(project_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _acwm_revision(project_root: Path) -> str:
    try:
        payload = json.loads(
            (project_root / "config" / "framework-lock.json").read_text(encoding="utf-8")
        )
        return str(payload["acwm"]["revision"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return "unknown"


def _count(value: float | int | None) -> int:
    if not isinstance(value, int):
        raise TypeError("pairwise count must be an integer")
    return value
