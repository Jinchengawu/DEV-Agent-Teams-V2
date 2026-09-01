"""Install the pinned BMAD/TEA archives into the local content-addressed store."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    FrozenMethodPackSet,
    MethodPackInstall,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="install-method-packs")
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--store", type=Path)
    arguments = parser.parse_args()

    project_root = Path(__file__).parents[1].resolve()
    lock_file = (arguments.lock or project_root / "config" / "method-packs-v050.json").resolve()
    data_root = Path(
        os.environ.get("AGENT_TEAM_OS_DATA_DIR", str(project_root / ".agent-team-os"))
    ).resolve()
    store_root = (arguments.store or data_root / "method-packs").resolve()
    configuration = json.loads(lock_file.read_text(encoding="utf-8"))
    raw_packages = configuration.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise RuntimeError("METHOD_PACK_LOCK_PACKAGES_MISSING")

    store = ContentAddressedMethodPackStore(store_root)
    installed: list[dict[str, object]] = []
    for raw in raw_packages:
        if not isinstance(raw, dict) or not isinstance(raw.get("install"), dict):
            raise RuntimeError("METHOD_PACK_LOCK_PACKAGE_INVALID")
        request = MethodPackInstall.model_validate(raw["install"])
        archive = _download(request)
        snapshot = store.install_archive(request, archive)
        if snapshot.content_sha256 != raw.get("expected_content_sha256"):
            raise RuntimeError("METHOD_PACK_CONTENT_HASH_DRIFT")
        if snapshot.qualification_sha256 != raw.get("expected_qualification_sha256"):
            raise RuntimeError("METHOD_PACK_QUALIFICATION_HASH_DRIFT")
        installed.append(
            {
                "package_name": snapshot.package_name,
                "package_version": snapshot.package_version,
                "archive_sha256": snapshot.archive_sha256,
                "content_sha256": snapshot.content_sha256,
                "qualification_sha256": snapshot.qualification_sha256,
                "store_uri": snapshot.store_uri,
            }
        )

    frozen_set = FrozenMethodPackSet(lock_file, store).snapshot()
    print(
        json.dumps(
            {
                "status": "ready",
                "store": str(store_root),
                "method_pack_set_sha256": frozen_set.qualification_sha256,
                "method_entries": sorted(frozen_set.method_entries),
                "packages": installed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _download(request: MethodPackInstall) -> bytes:
    with urllib.request.urlopen(request.tarball_uri, timeout=60) as response:  # noqa: S310
        if urlparse(response.geturl()).scheme != "https":
            raise RuntimeError("METHOD_PACK_DOWNLOAD_REDIRECT_NOT_HTTPS")
        archive = response.read(request.max_unpacked_bytes + 1)
    if len(archive) > request.max_unpacked_bytes:
        raise RuntimeError("METHOD_PACK_DOWNLOAD_SIZE_LIMIT_EXCEEDED")
    return archive


if __name__ == "__main__":
    raise SystemExit(main())
