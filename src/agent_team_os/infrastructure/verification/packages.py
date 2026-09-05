"""产品产物包只从内容寻址 Store 物化，不解释 URL，也不访问其他仓库。"""

from __future__ import annotations

import os
from pathlib import Path

from ...modules.artifacts import ArtifactReference, ContentAddressedArtifactStorage
from ...modules.workcells.verification_domain import (
    VerificationPackageManifestV1,
    VerificationPackageMember,
)
from ...modules.workcells.verification_packages import (
    package_error,
    valid_member,
    validate_members,
)
from ...shared.verification import VerificationQualificationV2


def create_package(
    store: ContentAddressedArtifactStorage,
    *,
    root: Path,
    qualification: VerificationQualificationV2,
    delivery_id: str,
    candidate_sha: str,
) -> ArtifactReference:
    contract = qualification.profile.output_contract
    if contract is None:
        raise package_error("当前 Profile 没有发布产物的权限。")
    members = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise package_error("产物包禁止符号链接。")
        if path.is_dir():
            continue
        name = path.relative_to(root).as_posix()
        if not valid_member(contract, name):
            raise package_error("产物目录包含未发布合同允许的文件。")
        size = path.stat().st_size
        if size > 20_000_000 - total:
            raise package_error("产物包超过字节预算。")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            payload = stream.read(20_000_000 - total + 1)
        if len(payload) != size or len(payload) > 20_000_000 - total:
            raise package_error("产物在读取期间变化或超过字节预算。")
        total += len(payload)
        members.append(
            VerificationPackageMember(
                path=name,
                content=store.put_bytes(payload, media_type="application/octet-stream"),
            )
        )
    manifest = VerificationPackageManifestV1(
        package_contract=contract,
        delivery_id=delivery_id,
        workcell_key=qualification.profile.workcell_key,
        candidate_sha=candidate_sha,
        profile_sha256=qualification.profile_sha256,
        qualification_sha256=qualification.qualification_sha256,
        members=tuple(members),
    )
    validate_members(manifest)
    return store.put_json(manifest.model_dump(mode="json"))


def materialize_package(
    store: ContentAddressedArtifactStorage,
    manifest: VerificationPackageManifestV1,
    destination: Path,
) -> None:
    validate_members(manifest)
    # 目标必须由产品新建；不覆盖已有目录、文件或符号链接。
    if destination.exists() or destination.is_symlink():
        raise package_error("物化目标已存在。")
    if any(parent.is_symlink() for parent in destination.parents):
        raise package_error("物化目标父目录含符号链接。")
    destination.mkdir(parents=True)
    for member in manifest.members:
        target = destination / member.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in target.parents if parent != destination.parent):
            raise package_error("物化目标含符号链接。")
        with target.open("xb") as stream:
            stream.write(
                store.get_bytes(
                    member.content, max_bytes=min(member.content.size_bytes, 20_000_000)
                )
            )
