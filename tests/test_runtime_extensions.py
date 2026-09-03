import base64
import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_team_os.api import create_app
from agent_team_os.control_plane import AgentInstanceCreate, ControlPlaneService, HealthResult
from agent_team_os.delivery import DeliveryCoordinator
from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.modules.agents import (
    AgentDeploymentCatalog,
    AgentDeploymentCreate,
    AgentProfileCatalog,
    AgentProfileCreate,
    AgentProfileSpec,
    ProviderManifestCatalog,
    SQLiteAgentDeploymentRepository,
    SQLiteAgentProfileRepository,
)
from agent_team_os.modules.extensions import (
    ContentAddressedMethodPackStore,
    MethodEntry,
    MethodPackInstall,
    RuntimeExtensionCatalog,
    RuntimeExtensionInstall,
    SQLiteRuntimeExtensionRepository,
)
from agent_team_os.shared.errors import ProductError
from agent_team_os.testing import DeterministicCodeExecutor, DeterministicPlanningService


def test_verified_method_pack_is_materialized_as_a_temporary_read_only_codex_overlay(
    tmp_path: Path,
) -> None:
    archive = _method_pack_archive(
        {
            "package/src/bmm-skills/ship/bmad-build/SKILL.md": b"# BMAD Build\n",
            "package/src/bmm-skills/ship/bmad-build/workflow.md": b"Build workflow\n",
            "package/src/scripts/render_skill.py": b"# renderer\n",
            "package/src/scripts/config_utils.py": b"# config helpers\n",
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}\n',
        }
    )
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    registry_integrity = "sha512-" + base64.b64encode(
        hashlib.sha512(archive).digest()
    ).decode("ascii")
    store = ContentAddressedMethodPackStore(tmp_path / "method-packs")

    snapshot = store.install_archive(
        MethodPackInstall(
            package_name="bmad-method",
            package_version="6.11.0",
            tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
            registry_integrity=registry_integrity,
            archive_sha256=archive_sha256,
            method_entries=(
                MethodEntry(
                    method_id="bmad-build",
                    source_path="src/bmm-skills/ship/bmad-build",
                ),
            ),
        ),
        archive,
    )

    assert snapshot.package_name == "bmad-method"
    assert snapshot.archive_sha256 == archive_sha256
    assert snapshot.qualification_sha256
    auth_file = tmp_path / "operator-auth.json"
    auth_file.write_text('{"auth_mode":"test"}\n', encoding="utf-8")
    auth_file.chmod(0o600)
    with store.runtime_overlay((snapshot,), codex_auth_file=auth_file) as overlay:
        skill = overlay.codex_home / "skills" / "bmad-build" / "SKILL.md"
        auth_reference = overlay.codex_home / "auth.json"
        assert skill.read_text(encoding="utf-8") == "# BMAD Build\n"
        assert (overlay.codex_home / "config.toml").read_text(encoding="utf-8") == (
            "[features]\nmulti_agent = false\n"
        )
        assert overlay.environment == {
            "AGENT_TEAM_OS_BMAD_RUNTIME_SOURCE": str(
                store._object_path(snapshot.content_sha256) / "src"  # noqa: SLF001
            ),
            "CODEX_HOME": str(overlay.codex_home),
        }
        assert skill.stat().st_mode & 0o222 == 0
        assert auth_reference.is_symlink()
        assert auth_reference.resolve() == auth_file.resolve()
        overlay_root = overlay.root
    assert not overlay_root.exists()
    assert auth_file.read_text(encoding="utf-8") == '{"auth_mode":"test"}\n'
    assert auth_file.stat().st_mode & 0o777 == 0o600

    auth_file.chmod(0o644)
    with (
        pytest.raises(ProductError) as error,
        store.runtime_overlay((snapshot,), codex_auth_file=auth_file),
    ):
        pass
    assert error.value.code == "CODEX_CREDENTIAL_REFERENCE_PERMISSIONS_INVALID"


def _method_pack_archive(files: dict[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return payload.getvalue()


def test_method_pack_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = _method_pack_archive(
        {
            "package/../escape.txt": b"escape",
            "package/package.json": b'{"name":"bmad-method","version":"6.11.0"}\n',
            "package/src/bmad/SKILL.md": b"# Entry\n",
        }
    )
    request = MethodPackInstall(
        package_name="bmad-method",
        package_version="6.11.0",
        tarball_uri="https://registry.npmjs.org/bmad-method/-/bmad-method-6.11.0.tgz",
        registry_integrity="sha512-"
        + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii"),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        method_entries=(MethodEntry(method_id="bmad", source_path="src/bmad"),),
    )

    with pytest.raises(ProductError) as error:
        ContentAddressedMethodPackStore(tmp_path / "store").install_archive(request, archive)

    assert error.value.code == "METHOD_PACK_ARCHIVE_PATH_INVALID"
    assert not (tmp_path / "escape.txt").exists()


class _ReadyProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        return HealthResult(status="ready", identity="codex-cli")


def _catalog(tmp_path: Path) -> RuntimeExtensionCatalog:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    return RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))


def test_installed_skill_requires_explicit_qualification(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="a" * 64,
            requested_permissions=("artifact:write", "workspace:read"),
        ),
        actor_id="admin",
    )

    assert installed.status == "installed"
    qualified = catalog.qualify(installed.id, expected_version=installed.version)
    assert qualified.status == "qualified"
    assert qualified.qualification_sha256 is not None


def test_extension_qualification_fails_closed_on_unsafe_permission(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="unsafe-design",
            name="不安全设计扩展",
            kind="skill",
            version="1.0.0",
            source_uri="skill://unsafe-design@1.0.0",
            revision_sha256="b" * 64,
            requested_permissions=("shell:arbitrary",),
        ),
        actor_id="admin",
    )

    failed = catalog.qualify(installed.id, expected_version=installed.version)
    assert failed.status == "failed"
    assert failed.qualification_errors == ("EXTENSION_PERMISSION_NOT_ALLOWED",)


def test_profile_extension_resolution_freezes_exact_qualified_revision(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    installed = catalog.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.2.0",
            source_uri="skill://open-design@1.2.0",
            revision_sha256="c" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    qualified = catalog.qualify(installed.id, expected_version=installed.version)

    snapshot = catalog.resolve({"id": "open-design", "kind": "skill", "version": ">=1,<2"})

    assert snapshot["id"] == "open-design"
    assert snapshot["version"] == "1.2.0"
    assert snapshot["revision_sha256"] == qualified.revision_sha256
    assert snapshot["qualification_sha256"] == qualified.qualification_sha256


def test_agent_deployment_freezes_qualified_profile_extension_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent-team-os.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    extensions = RuntimeExtensionCatalog(SQLiteRuntimeExtensionRepository(database))
    installed = extensions.install(
        RuntimeExtensionInstall(
            id="open-design",
            name="Open Design",
            kind="skill",
            version="1.0.0",
            source_uri="skill://open-design@1.0.0",
            revision_sha256="d" * 64,
            requested_permissions=("artifact:write",),
        ),
        actor_id="admin",
    )
    extensions.qualify(installed.id, expected_version=installed.version)
    profiles = AgentProfileCatalog(SQLiteAgentProfileRepository(database))
    spec = AgentProfileSpec.model_validate(
        {
            "schema_version": "1",
            "id": "ui-designer",
            "name": "UI 设计师",
            "instructions": {"custom_text": "输出可审查的设计规范"},
            "capabilities": [{"id": "frontend.implementation", "version": ">=1,<2"}],
            "policies": {
                "tool_policy_ref": "policy://design-tools@1",
                "resource_policy_ref": "policy://design-resources@1",
                "approval_policy_ref": "policy://design-approval@1",
                "memory_policy_ref": "policy://session-isolated@1",
                "delegation_policy_ref": "policy://no-delegation@1",
            },
            "extensions": {
                "runtime_extensions": [
                    {
                        "id": "open-design",
                        "kind": "skill",
                        "version": ">=1,<2",
                    }
                ]
            },
        }
    )
    created = profiles.create(AgentProfileCreate(spec=spec), actor_id="admin")
    validated = profiles.validate_draft(
        spec.id, expected_version=created.draft.version, actor_id="admin"
    )
    profiles.publish(spec.id, expected_version=validated.version, actor_id="admin")
    instances = ControlPlaneService(database, probe=_ReadyProbe())
    instance = instances.create_instance(
        AgentInstanceCreate(
            name="Codex 设计实例",
            runtime_type="codex-cli",
            connection={"command": "codex"},
        )
    )
    instance = __import__("asyncio").run(instances.check_instance(instance.id))
    deployments = AgentDeploymentCatalog(
        SQLiteAgentDeploymentRepository(database),
        profiles,
        instances,
        ProviderManifestCatalog(),
        extensions=extensions,
    )
    deployment = deployments.create(
        AgentDeploymentCreate(
            id="ui-design-codex",
            name="UI 设计 Codex",
            profile_id=spec.id,
            profile_revision=1,
            instance_id=instance.id,
            provider_id="codex-cli-provider",
        ),
        actor_id="admin",
    )
    qualified = deployments.qualify(deployment.id, deployment.version)

    assert qualified.qualification_status == "qualified"
    assert qualified.extension_snapshot[0]["id"] == "open-design"
    assert qualified.extension_snapshot[0]["revision_sha256"] == "d" * 64


def test_runtime_extension_public_interface_installs_and_qualifies(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    coordinator = DeliveryCoordinator(
        planning=DeterministicPlanningService(), executor=DeterministicCodeExecutor()
    )
    with TestClient(create_app(coordinator, runtime_extensions=catalog)) as client:
        installed = client.post(
            "/v1/runtime-extensions",
            json={
                "id": "open-design",
                "name": "Open Design",
                "kind": "skill",
                "version": "1.0.0",
                "source_uri": "skill://open-design@1.0.0",
                "revision_sha256": "e" * 64,
                "requested_permissions": ["artifact:write"],
            },
        )
        qualified = client.post(
            "/v1/runtime-extensions/open-design/qualify",
            json={"expected_version": installed.json()["version"]},
        )
        records = client.get("/v1/runtime-extensions")

    assert installed.status_code == 201
    assert qualified.json()["status"] == "qualified"
    assert records.json() == [qualified.json()]
