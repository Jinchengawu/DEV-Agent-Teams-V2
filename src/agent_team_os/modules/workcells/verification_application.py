"""验证方案由产品发布；工作区只选择方案，Agent 无权改写命令。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.verification import (
    VerificationDependencyIdentity,
    VerificationFileIdentity,
    VerificationProfile,
    VerificationProfileLike,
    VerificationProfileSnapshot,
    VerificationProfileV2,
    VerificationQualificationV2,
    VerificationSnapshot,
    VerificationToolIdentity,
)
from .verification_catalog_v2 import fullstack_profiles


class VerificationToolchain(Protocol):
    def inspect(self, name: str) -> VerificationToolIdentity: ...
    def inspect_dependencies(
        self, names: tuple[str, ...]
    ) -> tuple[VerificationDependencyIdentity, ...]: ...
    def inspect_files(
        self, workspace: Path, names: tuple[str, ...], *, workcell_key: str | None = None
    ) -> tuple[VerificationFileIdentity, ...]: ...


class VerificationProfileCatalog:
    def list(self) -> tuple[VerificationProfileLike, ...]:
        return (
            VerificationProfile(
                id="python-unittest-v1",
                revision=1,
                name="Python unittest（须发现测试）",
                commands=(
                    (
                        "python",
                        "-I",
                        "-B",
                        "-c",
                        "import sys, unittest; sys.path.insert(0, '.'); "
                        "unittest.main(module=None, "
                        "argv=['unittest', 'discover', '-s', 'tests', '-v'])",
                    ),
                ),
                timeout_seconds=300,
                environment={"PYTHONDONTWRITEBYTECODE": "1", "CI": "1"},
                result_contract="python-unittest-count-v1",
                tool_names=("python",),
            ),
            VerificationProfile(
                id="node-native-test-v1",
                revision=1,
                name="Node 原生测试（须发现测试）",
                commands=(("node", "--test", "--test-reporter=tap"),),
                timeout_seconds=300,
                environment={"CI": "1"},
                result_contract="node-tap-count-v1",
                tool_names=("node",),
            ),
        ) + fullstack_profiles()

    def get(self, profile_id: str) -> VerificationProfileLike:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise profile_error("WORKCELL_VERIFICATION_PROFILE_UNKNOWN", "产品尚未发布该验证方案。")

    def qualify(
        self,
        profile_id: str,
        toolchain: VerificationToolchain,
        *,
        workspace: Path | None = None,
    ) -> VerificationSnapshot:
        profile = self.get(profile_id)
        tools = tuple(toolchain.inspect(name) for name in profile.tool_names)
        profile_sha = sha256_json(profile.model_dump(mode="json"))
        if isinstance(profile, VerificationProfileV2):
            if workspace is None:
                raise profile_error(
                    "WORKCELL_VERIFICATION_WORKSPACE_REQUIRED", "V2 资格必须检查产品绑定仓库配置。"
                )
            dependencies = toolchain.inspect_dependencies(profile.dependency_names)
            files = toolchain.inspect_files(
                workspace, profile.config_paths, workcell_key=profile.workcell_key
            )
            payload = {
                "profile_sha256": profile_sha,
                "tools": [tool.model_dump(mode="json") for tool in tools],
                "dependencies": [item.model_dump(mode="json") for item in dependencies],
                "workspace_files": [item.model_dump(mode="json") for item in files],
            }
            return VerificationQualificationV2(
                profile=profile,
                profile_sha256=profile_sha,
                tools=tools,
                dependencies=dependencies,
                workspace_files=files,
                qualification_sha256=sha256_json(payload),
            )
        return VerificationProfileSnapshot(
            profile=profile,
            profile_sha256=profile_sha,
            tools=tools,
            qualification_sha256=sha256_json(
                {
                    "profile_sha256": profile_sha,
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                }
            ),
        )

    def validate(
        self,
        snapshot: VerificationSnapshot | None,
        toolchain: VerificationToolchain,
        *,
        workspace: Path | None = None,
    ) -> None:
        self.validate_frozen(snapshot)
        assert snapshot is not None
        if isinstance(snapshot, VerificationQualificationV2):
            if (
                snapshot.tools
                != tuple(toolchain.inspect(name) for name in snapshot.profile.tool_names)
                or snapshot.dependencies
                != toolchain.inspect_dependencies(snapshot.profile.dependency_names)
                or (
                    workspace is not None
                    and snapshot.workspace_files
                    != toolchain.inspect_files(
                        workspace,
                        snapshot.profile.config_paths,
                        workcell_key=snapshot.profile.workcell_key,
                    )
                )
            ):
                raise profile_error(
                    "WORKCELL_VERIFICATION_QUALIFICATION_CHANGED", "工具或冻结仓库配置已改变。"
                )
            return
        expected = self.qualify(snapshot.profile.id, toolchain)
        if snapshot != expected:
            raise profile_error(
                "WORKCELL_VERIFICATION_QUALIFICATION_CHANGED",
                "验证方案或工具链身份与产品当前资格不一致，请重新资格化并创建交付。",
            )

    def validate_frozen(self, snapshot: VerificationSnapshot | None) -> None:
        """检查历史冻结事实，不用今天的工具版本重写过去的资格结果。"""
        if snapshot is None:
            raise profile_error(
                "WORKCELL_VERIFICATION_PROFILE_REQUIRED", "工作区尚未冻结产品验证方案与工具链资格。"
            )
        profile = self.get(snapshot.profile.id)
        profile_sha = sha256_json(profile.model_dump(mode="json"))
        payload: dict[str, object] = {
            "profile_sha256": profile_sha,
            "tools": [tool.model_dump(mode="json") for tool in snapshot.tools],
        }
        if isinstance(snapshot, VerificationQualificationV2):
            if (
                not isinstance(profile, VerificationProfileV2)
                or tuple(item.name for item in snapshot.dependencies) != profile.dependency_names
                or tuple(item.path for item in snapshot.workspace_files) != profile.config_paths
            ):
                raise profile_error(
                    "WORKCELL_VERIFICATION_PROFILE_INVALID", "V2 资格字段与产品合同不符。"
                )
            payload.update(
                dependencies=[item.model_dump(mode="json") for item in snapshot.dependencies],
                workspace_files=[item.model_dump(mode="json") for item in snapshot.workspace_files],
            )
        elif isinstance(profile, VerificationProfileV2):
            raise profile_error(
                "WORKCELL_VERIFICATION_PROFILE_INVALID", "V2 Profile 不能使用 V1 资格。"
            )
        expected_qualification = sha256_json(payload)
        if (
            snapshot.profile != profile
            or snapshot.profile_sha256 != profile_sha
            or tuple(tool.name for tool in snapshot.tools) != profile.tool_names
            or snapshot.qualification_sha256 != expected_qualification
        ):
            raise profile_error(
                "WORKCELL_VERIFICATION_PROFILE_INVALID",
                "冻结资格与产品验证方案或工具链内容哈希不一致。",
            )


def validate_test_result(profile_id: str, output: str) -> bool:
    """退出码由执行器判断；此处额外拒绝零测试、全跳过和无结果合同。"""
    if profile_id == "python-unittest-v1":
        counts = re.findall(r"^Ran (\d+) tests? in [^\n]+$", output, re.MULTILINE)
        return bool(counts and int(counts[-1]) > 0 and re.search(r"(?:^|\n)OK\s*\Z", output))
    if profile_id == "node-native-test-v1":
        values = dict(
            re.findall(
                r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$", output, re.MULTILINE
            )
        )
        return (
            int(values.get("tests", "0")) > 0
            and int(values.get("pass", "0")) > 0
            and all(values.get(key) == "0" for key in ("fail", "cancelled", "skipped", "todo"))
        )
    return False


def profile_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="机器验证方案未就绪",
        detail=detail,
        repair="由项目管理员选择已发布的验证方案并验证工具链后重试。",
        status_code=409,
    )
