"""Workcell 产物包的纯合同与来源校验，可供 Release 复用。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import PurePosixPath

from ...shared.errors import ProductError
from ..artifacts import ArtifactReference, ContentAddressedArtifactStorage
from .verification_domain import (
    VerificationPackageManifestV1,
    VerificationPackagePublicationV1,
    VerificationReportV2,
)

PACKAGE_OWNERS = {
    "health-design-v1": "design",
    "health-frontend-dist-v1": "frontend",
    "health-backend-runtime-v1": "backend",
}


def package_error(detail: str) -> ProductError:
    return ProductError(
        code="WORKCELL_VERIFICATION_PACKAGE_INVALID",
        title="验证产物包无效",
        detail=detail,
        repair="重建并验证本次交付的来源产物包。",
        status_code=409,
    )


def valid_member(contract: str, name: str) -> bool:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or str(path) != name
        or "\\" in name
        or any(part in {".", ".."} for part in path.parts)
    ):
        return False
    if contract == "health-design-v1":
        return name in {"contract.json", "schema.json", "vectors.json"}
    if contract == "health-backend-runtime-v1":
        return name == "src/server.py"
    if contract == "health-frontend-dist-v1":
        return name == "index.html" or bool(
            re.fullmatch(r"assets/[A-Za-z0-9_.-]+\.(js|css|svg)", name)
        )
    return False


def validate_members(manifest: VerificationPackageManifestV1) -> None:
    names = [member.path for member in manifest.members]
    required = {
        "health-design-v1": {"contract.json", "schema.json", "vectors.json"},
        "health-backend-runtime-v1": {"src/server.py"},
        "health-frontend-dist-v1": {"index.html"},
    }.get(manifest.package_contract)
    if (
        required is None
        or manifest.workcell_key != PACKAGE_OWNERS[manifest.package_contract]
        or len(names) != len(set(names))
        or not required.issubset(names)
        or not all(valid_member(manifest.package_contract, name) for name in names)
        or sum(member.content.size_bytes for member in manifest.members) > 20_000_000
    ):
        raise package_error("产物包含未知、重复、越界成员，或缺少必需文件。")


def validate_package_bytes(
    store: ContentAddressedArtifactStorage, manifest: VerificationPackageManifestV1
) -> None:
    validate_members(manifest)
    contents = {
        member.path: store.get_bytes(
            member.content, max_bytes=min(member.content.size_bytes, 20_000_000)
        )
        for member in manifest.members
    }
    if manifest.package_contract == "health-frontend-dist-v1":

        class Assets(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.paths: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                values = dict(attrs)
                if tag == "script" and values.get("src"):
                    self.paths.append(str(values["src"]))
                if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
                    self.paths.append(str(values["href"]))

        parser = Assets()
        parser.feed(contents["index.html"].decode("utf-8"))
        if not parser.paths or any(
            path.removeprefix("/").removeprefix("./") not in contents for path in parser.paths
        ):
            raise package_error("前端 HTML 未引用本包中存在的脚本或样式。")


def resolve_publication(
    store: ContentAddressedArtifactStorage,
    reference: ArtifactReference,
    *,
    delivery_id: str,
    source_report: VerificationReportV2,
    verification_sha256: str,
) -> VerificationPackageManifestV1:
    publication = VerificationPackagePublicationV1.model_validate_json(
        store.get_bytes(reference, max_bytes=65_536)
    )
    if (
        publication.delivery_id != delivery_id
        or publication.delivery_id != source_report.delivery_id
        or publication.workcell_key != source_report.workcell_key
        or publication.candidate_sha != source_report.candidate_sha
        or publication.verification_sha256 != verification_sha256
        or publication.manifest != source_report.output_manifest
    ):
        raise package_error("产物发布与本次交付的已验证来源不一致。")
    manifest = VerificationPackageManifestV1.model_validate_json(
        store.get_bytes(publication.manifest, max_bytes=262_144)
    )
    if (
        manifest.delivery_id != delivery_id
        or manifest.workcell_key != source_report.workcell_key
        or manifest.candidate_sha != source_report.candidate_sha
        or manifest.profile_sha256 != source_report.profile_sha256
        or manifest.qualification_sha256 != source_report.qualification_sha256
    ):
        raise package_error("包来源与验证报告不一致。")
    validate_package_bytes(store, manifest)
    return manifest
