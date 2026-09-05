"""在产品临时目录验证冻结 Candidate，并发布 QA 可消费的内容寻址包。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from ...modules.artifacts import ContentAddressedArtifactStorage
from ...modules.workcells.verification_application import VerificationProfileCatalog
from ...modules.workcells.verification_domain import (
    VerificationReportV2,
    VerificationSourceV2,
    VerificationStepResultV2,
)
from ...modules.workcells.verification_evidence import (
    STEP_NAMES,
    command_values,
    passed_counts,
    render_command,
    result_counts,
    validate_report_v2,
)
from ...modules.workcells.verification_packages import package_error, resolve_publication
from ...shared.verification import VerificationQualificationV2
from ..git import ExternalCandidateEvidence
from .command_toolchain import LocalVerificationToolchain, verification_environment
from .packages import create_package, materialize_package
from .tool_environment import workspace_files


class VerificationCommandRunner(Protocol):
    async def __call__(
        self,
        command: tuple[str, ...],
        *,
        workspace: Path,
        timeout_seconds: int,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


def _git_bytes(workspace: Path, arguments: tuple[str, ...], limit: int) -> bytes:
    import os
    import select
    import time

    process = subprocess.Popen(
        ("git", "-C", str(workspace), *arguments), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + 15
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise package_error("读取冻结 Git 对象超时。")
            chunk = os.read(process.stdout.fileno(), min(65_536, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise package_error("冻结 Git 对象超过验证预算。")
            chunks.append(chunk)
        if process.wait(timeout=1):
            raise package_error("冻结 Git 对象无法读取。")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def copy_candidate(workspace: Path, revision: str, destination: Path) -> None:
    # 逐个读取不可变 Blob，避免 Git archive 的 export-ignore/export-subst 改变 Candidate 内容。
    tree = _git_bytes(workspace, ("ls-tree", "-r", "-z", revision), 1_000_000)
    entries = []
    total = 0
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, identity = metadata.decode().split()
        path = PurePosixPath(name.decode("utf-8"))
        if (
            mode not in {"100644", "100755"}
            or kind != "blob"
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in str(path)
        ):
            raise package_error("Candidate 包含越界路径、子模块或符号链接。")
        size = int(_git_bytes(workspace, ("cat-file", "-s", identity), 32))
        total += size
        if total > 30_000_000:
            raise package_error("Candidate 超过本验证方案的大小上限。")
        entries.append((path, identity, size))
    destination.mkdir()
    for path, identity, size in entries:
        target = destination / str(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = _git_bytes(workspace, ("cat-file", "blob", identity), size)
        if len(payload) != size:
            raise package_error("冻结 Blob 大小不一致。")
        with target.open("xb") as stream:
            stream.write(payload)


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.assets.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.assets.append(str(values["href"]))


def validate_build(root: Path) -> None:
    parser = _Assets()
    parser.feed((root / "index.html").read_text())
    if not parser.assets:
        raise package_error("前端构建缺少实际脚本资源。")
    for name in parser.assets:
        relative = name.removeprefix("/").removeprefix("./")
        path = root / relative
        if (
            not relative.startswith("assets/")
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or path.is_symlink()
        ):
            raise package_error("构建引用了缺失或非本包资源。")


def _simple_result(step: str) -> dict[str, object]:
    return {"discovered": 1, "passed": 1, "failed": 0, "skipped": 0, "case_ids": [step]}


async def verify_fullstack(
    *,
    workspace: Path,
    candidate: ExternalCandidateEvidence,
    qualification: VerificationQualificationV2,
    workcell_key: str,
    delivery_id: str,
    sources: tuple[VerificationSourceV2, ...],
    store: ContentAddressedArtifactStorage,
    run_command: VerificationCommandRunner,
    redact: Callable[[str], str],
) -> tuple[Literal["passed", "failed"], dict[str, object]]:
    catalog = VerificationProfileCatalog()
    catalog.validate(qualification, LocalVerificationToolchain(), workspace=workspace)
    if qualification.profile.workcell_key != workcell_key:
        raise package_error("Profile 不适用于当前 Workcell。")
    with tempfile.TemporaryDirectory(prefix="agent-team-os-verification-v2-") as temporary:
        root = Path(temporary).resolve()
        source = root / "workspace"
        copy_candidate(workspace, candidate.candidate_revision, source)
        if (
            workspace_files(source, qualification.profile.config_paths)
            != qualification.workspace_files
        ):
            raise package_error("冻结 Candidate 中的配置与工具资格不一致。")
        (root / "results").mkdir()
        (root / "inputs").mkdir()
        (root / "config").mkdir()
        values = command_values(qualification, root, 0)
        runner = Path(values["runner"])
        for name in ("vite.config.mjs", "vitest.config.mjs"):
            shutil.copyfile(runner / name, root / "config" / name)
        if "node_modules" in values:
            (source / "node_modules").symlink_to(values["node_modules"], target_is_directory=True)
        resolved_contracts: set[str] = set()
        input_references = []
        for upstream in sources:
            validate_report_v2(upstream.report, upstream.qualification, store)
            manifest = resolve_publication(
                store,
                upstream.publication,
                delivery_id=delivery_id,
                source_report=upstream.report,
                verification_sha256=upstream.verification_sha256,
            )
            contract = manifest.package_contract
            if (
                contract not in qualification.profile.input_contracts
                or contract in resolved_contracts
            ):
                raise package_error("存在未知或重复的上游产物合同。")
            materialize_package(store, manifest, root / "inputs" / contract)
            resolved_contracts.add(contract)
            input_references.append(upstream.publication)
        if resolved_contracts != set(qualification.profile.input_contracts):
            raise package_error("缺少本 Profile 要求的上游已验证产物。")
        environment = verification_environment(qualification.profile.environment)
        environment.update(
            ATOS_VERIFICATION_WORKSPACE=str(source), ATOS_VERIFICATION_CACHE=str(root / "cache")
        )
        if "chromium" in values:
            environment["ATOS_CHROMIUM_EXECUTABLE"] = values["chromium"]
        if "jsonschema" in values or "playwright" in values:
            environment["ATOS_VERIFICATION_PYTHON_SITE"] = values.get(
                "jsonschema", values.get("playwright", "")
            )
        steps = []
        successful = True
        for index, step in enumerate(STEP_NAMES[qualification.profile.id]):
            command = render_command(qualification, root, index)
            result_file = root / "results" / f"{index}.json"
            try:
                completed = await run_command(
                    command,
                    workspace=source,
                    timeout_seconds=qualification.profile.timeout_seconds,
                    environment=environment,
                )
                log_text = completed.stdout + completed.stderr
                exit_code = completed.returncode
                status: Literal["passed", "failed", "timed_out"] = (
                    "passed" if exit_code == 0 else "failed"
                )
                if step in {"typecheck", "build"} and exit_code == 0:
                    if step == "build":
                        validate_build(root / "build")
                    result_file.write_text(json.dumps(_simple_result(step)))
            except subprocess.TimeoutExpired:
                log_text, exit_code, status = "产品验证步骤超时；进程组已清理。", None, "timed_out"
            raw: object = None
            result_ref = None
            if result_file.is_file() and not result_file.is_symlink():
                try:
                    with result_file.open("rb") as stream:
                        payload = stream.read(2_000_001)
                    if len(payload) > 2_000_000:
                        raise package_error("验证结果超过 2 MiB 预算。")
                    raw = json.loads(payload)
                    result_ref = store.put_json(raw)
                except (ValueError, OSError):
                    pass
            counts = result_counts(qualification.profile.id, step, raw)
            passed = exit_code == 0 and passed_counts(step, counts)
            if not passed and status == "passed":
                status = "failed"
            # 只继承非敏感环境；额外脱敏由上层通用日志策略保持一致。
            log = store.put_bytes(redact(log_text).encode(), media_type="text/plain")
            steps.append(
                VerificationStepResultV2(
                    step=step,
                    command=command,
                    exit_code=exit_code,
                    status=status,
                    discovered=counts[0],
                    passed=counts[1],
                    failed=counts[2],
                    skipped=counts[3],
                    case_ids=counts[4],
                    result_contract_passed=passed,
                    result=result_ref,
                    log=log,
                )
            )
            if not passed:
                successful = False
                break
        manifest_ref = None
        if successful:
            # 执行期间工具变更也不得产生成功证据。
            catalog.validate(qualification, LocalVerificationToolchain())
            if qualification.profile.output_contract is not None:
                output = root / "output"
                if workcell_key == "frontend":
                    output = root / "build"
                else:
                    output.mkdir()
                    names = (
                        ("contract.json", "schema.json", "vectors.json")
                        if workcell_key == "design"
                        else ("src/server.py",)
                    )
                    base = source / "design" if workcell_key == "design" else source
                    for name in names:
                        target = output / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(base / name, target)
                manifest_ref = create_package(
                    store,
                    root=output,
                    qualification=qualification,
                    delivery_id=delivery_id,
                    candidate_sha=candidate.candidate_revision,
                )
        report = VerificationReportV2(
            delivery_id=delivery_id,
            workcell_key=workcell_key,
            candidate_sha=candidate.candidate_revision,
            diff_sha256=candidate.diff_sha256,
            profile_sha256=qualification.profile_sha256,
            qualification_sha256=qualification.qualification_sha256,
            execution_root=str(root),
            steps=tuple(steps),
            inputs=tuple(input_references),
            output_manifest=manifest_ref,
            cleanup_completed=True,
        )
        if successful:
            validate_report_v2(report, qualification, store)
        return ("passed" if successful else "failed"), report.model_dump(mode="json")
