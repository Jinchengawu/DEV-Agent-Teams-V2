"""验证方案由产品发布；工作区只选择方案，Agent 无权改写命令。"""

from __future__ import annotations

import re
from typing import Protocol

from ...shared.errors import ProductError
from ...shared.hashes import sha256_json
from ...shared.verification import (
    VerificationProfile,
    VerificationProfileSnapshot,
    VerificationToolIdentity,
)


class VerificationToolchain(Protocol):
    def inspect(self, name: str) -> VerificationToolIdentity: ...


class VerificationProfileCatalog:
    def list(self) -> tuple[VerificationProfile, ...]:
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
        )

    def get(self, profile_id: str) -> VerificationProfile:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise profile_error("WORKCELL_VERIFICATION_PROFILE_UNKNOWN", "产品尚未发布该验证方案。")

    def qualify(
        self, profile_id: str, toolchain: VerificationToolchain
    ) -> VerificationProfileSnapshot:
        profile = self.get(profile_id)
        tools = tuple(toolchain.inspect(name) for name in profile.tool_names)
        profile_sha = sha256_json(profile.model_dump(mode="json"))
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
        snapshot: VerificationProfileSnapshot | None,
        toolchain: VerificationToolchain,
    ) -> None:
        self.validate_frozen(snapshot)
        assert snapshot is not None
        expected = self.qualify(snapshot.profile.id, toolchain)
        if snapshot != expected:
            raise profile_error(
                "WORKCELL_VERIFICATION_QUALIFICATION_CHANGED",
                "验证方案或工具链身份与产品当前资格不一致，请重新资格化并创建交付。",
            )

    def validate_frozen(self, snapshot: VerificationProfileSnapshot | None) -> None:
        """检查历史冻结事实，不用今天的工具版本重写过去的资格结果。"""
        if snapshot is None:
            raise profile_error(
                "WORKCELL_VERIFICATION_PROFILE_REQUIRED", "工作区尚未冻结产品验证方案与工具链资格。"
            )
        profile = self.get(snapshot.profile.id)
        profile_sha = sha256_json(profile.model_dump(mode="json"))
        expected_qualification = sha256_json(
            {
                "profile_sha256": profile_sha,
                "tools": [tool.model_dump(mode="json") for tool in snapshot.tools],
            }
        )
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
