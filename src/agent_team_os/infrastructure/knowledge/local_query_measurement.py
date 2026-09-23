"""只读本地 tokenizer 接线；无默认模型、下载、网络或估算 fallback。"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ...modules.knowledge.query_plan import InputMeasurement, QueryBudget, QueryInputQualification
from ...shared.hashes import Sha256, sha256_bytes, sha256_json


class ExactLocalTokenizer(Protocol):
    implementation_sha256: Sha256
    normalization: str
    special_token_contract: str

    def count(self, text: str) -> int:
        """按冻结 normalization 精确计数，含 special tokens；必须纯本地。"""
        ...


class LocalMeasurementManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["local-query-measurement-v1"]
    tokenizer_kind: str = Field(min_length=1)
    implementation_sha256: Sha256
    normalization: str = Field(min_length=1)
    special_token_contract: str = Field(min_length=1)
    tokenizer_contract: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    base_qualification_sha256: Sha256
    embedding_adapter_revision: str = Field(min_length=1)
    evidence_kind: Literal["live", "deterministic"]
    assets: dict[str, Sha256] = Field(min_length=1)
    capacity_file: str
    receipt_file: str
    budget: QueryBudget


TokenizerFactory = Callable[[LocalMeasurementManifest, Mapping[str, bytes]], ExactLocalTokenizer]


def _read_asset(root: Path, relative: str) -> bytes:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in relative:
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    if not target.is_file() or target.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    try:
        return target.read_bytes()
    except OSError:
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID") from None


class LocalQueryMeasurement:
    """可信组合根注入实现与证据白名单；manifest 自报身份不是信任根。

    factories 的实现必须由已锁定产品依赖提供；其身份由该实现报告并与固定 manifest
    对照。这里不动态 import 任意模块，也不运行 manifest 中的代码或命令。
    """

    def __init__(
        self, root: Path, manifest_sha256: Sha256, manifest: LocalMeasurementManifest,
        tokenizer: ExactLocalTokenizer,
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.identity_sha256 = manifest_sha256
        self.tokenizer = tokenizer

    def _verify_assets(self) -> None:
        if sha256_bytes(_read_asset(self.root, "manifest.json")) != self.identity_sha256:
            raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
        for name, digest in self.manifest.assets.items():
            if sha256_bytes(_read_asset(self.root, name)) != digest:
                raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
        if (self.tokenizer.implementation_sha256 != self.manifest.implementation_sha256
                or self.tokenizer.normalization != self.manifest.normalization
                or self.tokenizer.special_token_contract != self.manifest.special_token_contract):
            raise ValueError("KNOWLEDGE_QUERY_TOKENIZER_IDENTITY_INVALID")

    def verify_qualification(self, qualification: QueryInputQualification) -> bool:
        try:
            self._verify_assets()
            m = self.manifest
            return (
                qualification.status == "qualified"
                and qualification.evidence_kind == m.evidence_kind
                and qualification.base_qualification_sha256 == m.base_qualification_sha256
                and qualification.model_digest == m.model_digest
                and qualification.embedding_adapter_revision == m.embedding_adapter_revision
                and qualification.measurement_identity_sha256 == self.identity_sha256
                and qualification.tokenizer_assets_sha256 == sha256_json(m.assets)
                and qualification.tokenizer_contract == m.tokenizer_contract
                and qualification.capacity_evidence_sha256 == m.assets[m.capacity_file]
                and qualification.qualification_receipt_sha256 == m.assets[m.receipt_file]
                and qualification.budget == m.budget
            )
        except (ValueError, OSError, KeyError):
            return False

    def measure(self, text: str) -> InputMeasurement:
        self._verify_assets()
        try:
            count = self.tokenizer.count(text)
            if type(count) is not int or count < 0:
                raise ValueError("invalid count")
            return InputMeasurement(tokens=count, utf8_bytes=len(text.encode("utf-8")),
                                    identity_sha256=self.identity_sha256)
        except Exception:
            raise ValueError("KNOWLEDGE_QUERY_MEASUREMENT_INVALID") from None


def load_local_query_measurement(
    root: Path,
    *,
    manifest_sha256: Sha256,
    factories: Mapping[str, TokenizerFactory] | None = None,
    trusted_evidence_sha256: frozenset[str] = frozenset(),
) -> LocalQueryMeasurement:
    """不含内置 tokenizer。未注册/未固定/未独立信任的证据一律拒绝。"""
    if root.is_symlink():
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    root = root.resolve()
    raw = _read_asset(root, "manifest.json")
    if sha256_bytes(raw) != manifest_sha256:
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    try:
        manifest = LocalMeasurementManifest.model_validate_json(raw)
    except ValueError:
        raise ValueError("KNOWLEDGE_QUERY_MANIFEST_INVALID") from None
    factory = (factories or {}).get(manifest.tokenizer_kind)
    if factory is None:
        raise ValueError("KNOWLEDGE_QUERY_TOKENIZER_UNAVAILABLE")
    assets = {name: _read_asset(root, name) for name in manifest.assets}
    if any(sha256_bytes(content) != manifest.assets[name] for name, content in assets.items()):
        raise ValueError("KNOWLEDGE_QUERY_ASSET_INVALID")
    if any(manifest.assets.get(name) not in trusted_evidence_sha256
           for name in (manifest.capacity_file, manifest.receipt_file)):
        raise ValueError("KNOWLEDGE_QUERY_EVIDENCE_UNTRUSTED")
    try:
        capacity = json.loads(assets[manifest.capacity_file])
        receipt = json.loads(assets[manifest.receipt_file])
        valid = (
            capacity["model_digest"] == manifest.model_digest
            and type(capacity["unit_tokens"]) is int
            and type(capacity["unit_bytes"]) is int
            and manifest.budget.unit_tokens <= capacity["unit_tokens"]
            and manifest.budget.unit_bytes <= capacity["unit_bytes"]
            and receipt["base_qualification_sha256"] == manifest.base_qualification_sha256
            and receipt["model_digest"] == manifest.model_digest
            and receipt["tokenizer_implementation_sha256"] == manifest.implementation_sha256
            and receipt["dataset_passed"] is True
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError("KNOWLEDGE_QUERY_EVIDENCE_INVALID")
    try:
        tokenizer = factory(manifest.model_copy(deep=True), assets)
    except Exception:
        raise ValueError("KNOWLEDGE_QUERY_TOKENIZER_UNAVAILABLE") from None
    adapter = LocalQueryMeasurement(root, manifest_sha256, manifest, tokenizer)
    adapter._verify_assets()
    return adapter


def local_query_measurement_from_environment(
    *,
    environ: Mapping[str, str] | None = None,
    factories: Mapping[str, TokenizerFactory] | None = None,
) -> LocalQueryMeasurement | None:
    """只读取三个专用、非秘密配置键；没有已锁定实现时保持不可用。

    EVIDENCE_SHA256S 是操作者独立审核后的 receipt/capacity SHA 白名单，不能从
    manifest 自动信任。生产没有默认实现 registry，显式 factory 仅由可信组合根提供。
    """
    config = os.environ if environ is None else environ
    root = config.get("AGENT_TEAM_OS_QUERY_MEASUREMENT_ROOT")
    digest = config.get("AGENT_TEAM_OS_QUERY_MEASUREMENT_MANIFEST_SHA256")
    evidence = config.get("AGENT_TEAM_OS_QUERY_MEASUREMENT_EVIDENCE_SHA256S")
    if not root or not digest or not evidence:
        return None
    try:
        trusted = frozenset(Sha256.validate(item.strip()) for item in evidence.split(","))
        return load_local_query_measurement(
            Path(root), manifest_sha256=Sha256.validate(digest), factories=factories,
            trusted_evidence_sha256=trusted,
        )
    except (ValueError, OSError):
        return None
