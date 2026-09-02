from __future__ import annotations

import argparse
import json
import math
import platform
import resource
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.infrastructure.knowledge import SQLiteVectorIndexAdapter
from agent_team_os.modules.artifacts import ContentAddressedArtifactStorage
from agent_team_os.modules.identity import (
    BootstrapRequest,
    IdentityService,
    SQLiteIdentityRepository,
)
from agent_team_os.modules.knowledge import (
    EmbeddingModelDescriptor,
    EmbeddingQualificationRequest,
    KnowledgeActor,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexManager,
    KnowledgeIndexProfileCreate,
    KnowledgeProviderKind,
    KnowledgeSyncJobRequest,
    ProviderNode,
    ProviderNodeKind,
    ProviderSnapshot,
    ProviderSpace,
    RetrievalEvaluationPolicyCreate,
    RetrievalPolicyCreate,
    SQLiteKnowledgeIndexRepository,
    SQLiteTenantKnowledgeRepository,
    TenantConnectionCreate,
    TenantKnowledgeManager,
    TenantProviderBindingCreate,
)
from agent_team_os.modules.knowledge.index_ports import EmbeddingPort
from agent_team_os.modules.projects import (
    ProjectCatalog,
    ProjectCreate,
    SQLiteProjectRepository,
)
from agent_team_os.shared.hashes import sha256_json
from agent_team_os.shared.permissions import Role


class BenchmarkWorkspaceProvisioner:
    def provision(self, repository_ref: str) -> str:
        return f"benchmark:{repository_ref}"

    def reset(self, repository_ref: str) -> str:
        return f"benchmark-reset:{repository_ref}"

    def revision(self, repository_ref: str) -> str:
        return f"benchmark-head:{repository_ref}"


class CapacityDatasetProvider:
    def __init__(self, chunk_count: int) -> None:
        self.chunk_count = chunk_count

    def list_spaces(self) -> tuple[ProviderSpace, ...]:
        return (ProviderSpace(external_id="capacity-space", title="容量基准"),)

    def list_nodes(self, external_space_id: str) -> tuple[ProviderNode, ...]:
        return (
            ProviderNode(
                external_id="capacity-document",
                external_space_id=external_space_id,
                source_id="docx:capacity-benchmark",
                title="100,000 Chunk 容量基准",
                kind=ProviderNodeKind.DOCUMENT,
                provider_revision="capacity-revision-v1",
            ),
        )

    def fetch_snapshot(self, source_id: str) -> ProviderSnapshot:
        blocks = [
            {
                "block_id": f"capacity-{index:06d}",
                "text": (
                    f"Agent-Team-OS 知识索引容量基准段落 {index:06d}。"
                    "Frontend Backend QA Design 四仓交付仅通过 Artifact 交换跨仓信息，"
                    "REMOTE_MAIN_APPLY_NOT_ALLOWED 必须 Fail Closed。"
                ),
            }
            for index in range(self.chunk_count)
        ]
        normalized = cast(
            JsonValue,
            {"type": "feishu-docx-blocks-v1", "blocks": blocks},
        )
        return ProviderSnapshot(
            source_id=source_id,
            provider_revision="capacity-revision-v1",
            content_type="application/json",
            normalized_content=normalized,
            normalized_text="\n".join(str(block["text"]) for block in blocks),
            content_sha256=sha256_json(normalized),
            source_url="https://example.invalid/wiki/capacity-benchmark",
            fetched_at=datetime.now(UTC),
        )


class StaticResolver:
    def __init__(self, provider: CapacityDatasetProvider) -> None:
        self.provider = provider

    def resolve(self, _connection: object) -> CapacityDatasetProvider:
        return self.provider


class DeterministicCapacityEmbeddingPort(EmbeddingPort):
    adapter_revision = "deterministic-capacity-embedding-v1"

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.vector = (1.0,) + (0.0,) * (dimension - 1)

    def describe(self, model_name: str) -> EmbeddingModelDescriptor:
        return EmbeddingModelDescriptor(
            model_name=model_name,
            model_digest=f"sha256:deterministic-capacity-{self.dimension}",
        )

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model_name: str,
        truncate: bool,
    ) -> tuple[tuple[float, ...], ...]:
        del model_name
        if truncate:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_TRUNCATION_FORBIDDEN")
        return tuple(self.vector for _text in texts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在当前机器上执行非 SLA 的 Knowledge Index 容量基准。"
    )
    parser.add_argument("--chunks", type=int, default=100_000)
    parser.add_argument("--dimension", type=int, default=1_024)
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.chunks < 1 or arguments.chunks > 100_000:
        parser.error("--chunks 必须在 1..100000 之间")
    if arguments.dimension < 1:
        parser.error("--dimension 必须大于 0")
    if arguments.queries < 1:
        parser.error("--queries 必须大于 0")

    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="agent-team-os-capacity-") as directory:
        result = _run(
            project_root,
            Path(directory),
            chunk_count=arguments.chunks,
            dimension=arguments.dimension,
            query_count=arguments.queries,
        )
    arguments.json_report.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown_report.parent.mkdir(parents=True, exist_ok=True)
    arguments.json_report.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    arguments.markdown_report.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _run(
    project_root: Path,
    data_dir: Path,
    *,
    chunk_count: int,
    dimension: int,
    query_count: int,
) -> dict[str, Any]:
    database = data_dir / "benchmark.sqlite"
    MigrationRunner(database, project_root / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    admin = identity.bootstrap(
        BootstrapRequest(password=f"B9-{secrets.token_urlsafe(24)}")
    )
    actor = KnowledgeActor(user_id=admin.id, role=Role.ADMINISTRATOR)
    projects = ProjectCatalog(
        SQLiteProjectRepository(database),
        BenchmarkWorkspaceProvisioner(),
    )
    projects.create(
        ProjectCreate(
            id="capacity-benchmark",
            name="Knowledge Index Capacity Benchmark",
            default_pipeline_revision_id="backend-delivery:1",
        ),
        admin.id,
    )
    artifacts = ContentAddressedArtifactStorage(data_dir / "artifacts")
    tenant = TenantKnowledgeManager(
        SQLiteTenantKnowledgeRepository(database),
        provider_resolver=StaticResolver(CapacityDatasetProvider(chunk_count)),
        artifact_storage=artifacts,
    )
    connection = tenant.create_connection(
        actor,
        TenantConnectionCreate(
            provider_kind=KnowledgeProviderKind.FEISHU,
            display_name="Capacity Fixture",
            app_id_ref="env:BENCHMARK_APP_ID",
            app_secret_ref="env:BENCHMARK_APP_SECRET",
        ),
    )
    connection = tenant.diagnose_connection(actor, connection.id)
    binding = tenant.create_binding(
        actor,
        TenantProviderBindingCreate(
            connection_id=connection.id,
            display_name="Capacity Fixture Space",
            external_space_id="capacity-space",
        ),
    )
    tenant.request_sync(
        actor,
        "capacity-benchmark",
        request=KnowledgeSyncJobRequest(
            binding_id=binding.id,
            source_id="docx:capacity-benchmark",
            idempotency_key="capacity-benchmark-v1",
        ),
    )

    vector_adapter = SQLiteVectorIndexAdapter()
    embeddings = DeterministicCapacityEmbeddingPort(dimension)
    indexes = KnowledgeIndexManager(
        SQLiteKnowledgeIndexRepository(database),
        tenant_repository=tenant.repository,
        artifact_storage=artifacts,
        index_root=data_dir / "indexes",
        embedding_port=embeddings,
        vector_index_port=vector_adapter,
    )
    profile = indexes.publish_index_profile(
        actor,
        KnowledgeIndexProfileCreate(
            id="capacity-profile-v1",
            display_name="100k Capacity Profile",
            embedding_model_name="deterministic-capacity-1024",
            max_documents=5_000,
            max_chunks=100_000,
            capacity_warning_ratio=0.8,
        ),
    )
    qualification = indexes.qualify_embedding(
        actor,
        EmbeddingQualificationRequest(model_name=profile.embedding_model_name),
    )
    policy = indexes.publish_retrieval_policy(
        actor,
        RetrievalPolicyCreate(
            id="capacity-retrieval-v1",
            display_name="100k Capacity Retrieval",
            index_profile_revision_id=profile.id,
            lexical_candidates=40,
            vector_candidates=40,
            top_k=8,
            rrf_k=60,
            max_context_bytes=65_536,
        ),
    )
    indexes.publish_evaluation_policy(
        actor,
        RetrievalEvaluationPolicyCreate(
            id="capacity-measurement-envelope-v1",
            retrieval_policy_revision_id=policy.id,
            index_profile_revision_id=profile.id,
            dataset_manifest_sha256=sha256_json(
                {"kind": "capacity-measurement-only", "chunks": chunk_count}
            ),
            recall_at_k_min=0.0,
            zero_hit_rate_max=1.0,
            error_rate_max=1.0,
            p95_latency_ms_max=600_000,
            peak_rss_bytes_max=max(_physical_memory_bytes(), 1),
            target_hardware=_hardware_summary(),
        ),
    )

    build_started = time.perf_counter()
    built = indexes.build(
        actor,
        KnowledgeIndexBuildRequest(
            provider_binding_id=binding.id,
            index_profile_revision_id=profile.id,
            embedding_qualification_id=qualification.id,
        ),
    )
    build_elapsed_seconds = time.perf_counter() - build_started
    index_path = data_dir / "indexes" / f"{built.id}.sqlite"

    restarted = KnowledgeIndexManager(
        indexes.repository,
        tenant_repository=tenant.repository,
        artifact_storage=artifacts,
        index_root=data_dir / "indexes",
        embedding_port=embeddings,
        vector_index_port=vector_adapter,
    )
    cold_started = time.perf_counter()
    restarted._index_path(built)  # noqa: SLF001 - benchmark restart integrity cost
    cold_integrity_ms = (time.perf_counter() - cold_started) * 1_000

    query_vector = embeddings.embed(
        ("Agent-Team-OS 四仓 Artifact Fail Closed",),
        model_name=profile.embedding_model_name,
        truncate=False,
    )[0]
    query_latencies_ms: list[float] = []
    stable_hit_ids: tuple[str, ...] | None = None
    for _ in range(query_count + 3):
        started = time.perf_counter()
        hits = restarted._query_index(  # noqa: SLF001 - benchmark exact engine path
            built,
            "Agent-Team-OS 四仓 Artifact Fail Closed",
            query_vector,
            ("docx:capacity-benchmark",),
            policy,
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
        hit_ids = tuple(hit.chunk_id for hit in hits)
        if stable_hit_ids is None:
            stable_hit_ids = hit_ids
        elif hit_ids != stable_hit_ids:
            raise RuntimeError("KNOWLEDGE_BENCHMARK_RESULT_NOT_DETERMINISTIC")
        if len(query_latencies_ms) < query_count and _ >= 3:
            query_latencies_ms.append(elapsed_ms)

    stat = index_path.stat()
    descriptor = vector_adapter.describe()
    return {
        "schema_version": "knowledge-index-capacity-benchmark-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "proof_scope": "deterministic_capacity_benchmark",
        "official_benchmark": False,
        "production_sla": False,
        "live_feishu": False,
        "live_ollama": False,
        "source_revision": _command(project_root, "git", "rev-parse", "HEAD"),
        "working_tree_dirty": bool(_command(project_root, "git", "status", "--porcelain")),
        "hardware": {
            "summary": _hardware_summary(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "physical_memory_bytes": _physical_memory_bytes(),
            "logical_cpu_count": _logical_cpu_count(),
        },
        "vector_engine": {
            "name": descriptor.engine_name,
            "version": descriptor.engine_version,
            "adapter_revision": descriptor.adapter_revision,
            "dimension": dimension,
            "embedding_adapter_revision": embeddings.adapter_revision,
        },
        "dataset": {
            "document_count": built.document_count,
            "chunk_count": built.chunk_count,
            "max_chunks": profile.max_chunks,
            "capacity_status": built.capacity_status,
        },
        "metrics": {
            "build_elapsed_seconds": round(build_elapsed_seconds, 3),
            "index_size_bytes": stat.st_size,
            "cold_integrity_verification_ms": round(cold_integrity_ms, 3),
            "query_count": query_count,
            "query_latency_ms": {
                "min": round(min(query_latencies_ms), 3),
                "p50": round(_percentile(query_latencies_ms, 0.50), 3),
                "p95": round(_percentile(query_latencies_ms, 0.95), 3),
                "p99": round(_percentile(query_latencies_ms, 0.99), 3),
                "max": round(max(query_latencies_ms), 3),
            },
            "peak_rss_bytes": _peak_rss_bytes(),
            "stable_hit_count": len(stable_hit_ids or ()),
        },
        "limitations": [
            "数据集与 Embedding 均为 Deterministic Fixture，不证明真实飞书或 Ollama 可用性。",
            "结果只是当前开发机容量与时延观测，不是生产 SLA。",
            f"向量为确定性 {dimension} 维测试值，"
            "不衡量 bge-m3 生成吞吐或语义质量。",
        ],
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _peak_rss_bytes() -> int:
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def _hardware_summary() -> str:
    cpu = _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown-cpu"
    memory_gib = _physical_memory_bytes() / (1024**3)
    return f"{cpu}; {memory_gib:.1f} GiB RAM; {platform.system()} {platform.release()}"


def _physical_memory_bytes() -> int:
    value = _sysctl("hw.memsize")
    return int(value) if value and value.isdigit() else 0


def _logical_cpu_count() -> int:
    value = _sysctl("hw.logicalcpu")
    return int(value) if value and value.isdigit() else (os_cpu_count() or 0)


def os_cpu_count() -> int | None:
    import os

    return os.cpu_count()


def _sysctl(key: str) -> str:
    try:
        return subprocess.run(
            ("/usr/sbin/sysctl", "-n", key),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _command(root: Path, *command: str) -> str:
    return subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def _markdown(result: dict[str, Any]) -> str:
    hardware = result["hardware"]
    dataset = result["dataset"]
    metrics = result["metrics"]
    latency = metrics["query_latency_ms"]
    return f"""# Knowledge Index 100,000 Chunk 容量基准

> 证据等级：`deterministic_capacity_benchmark`。本报告不是官方 Benchmark，不是生产 SLA，
> 也不证明真实飞书或 Ollama 可用。

| 项目 | 实测值 |
|---|---:|
| 机器 | `{hardware['summary']}` |
| Python / SQLite | `{hardware['python']}` / `{hardware['sqlite']}` |
| Document / Chunk | {dataset['document_count']} / {dataset['chunk_count']:,} |
| Vector Dimension | {result['vector_engine']['dimension']} |
| Index Size | {metrics['index_size_bytes']:,} bytes |
| Build | {metrics['build_elapsed_seconds']:.3f} s |
| Restart Cold Integrity | {metrics['cold_integrity_verification_ms']:.3f} ms |
| Query p50 / p95 / p99 | {latency['p50']:.3f} / {latency['p95']:.3f} / {latency['p99']:.3f} ms |
| Peak RSS | {metrics['peak_rss_bytes']:,} bytes |
| Capacity Status | `{dataset['capacity_status']}` |

## 边界

- Dataset 和 {result['vector_engine']['dimension']} 维 Embedding 均为 Deterministic Fixture。
- Query 观测包含真实 SQLite FTS5、sqlite-vec cosine 扫描和 RRF，
  但不包含网络与真实模型生成时间。
- Source Revision 为 `{result['source_revision']}`，
  执行时工作树脏状态为 `{result['working_tree_dirty']}`。
"""


if __name__ == "__main__":
    main()
