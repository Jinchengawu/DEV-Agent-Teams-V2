from __future__ import annotations

from pathlib import Path

import pytest

from agent_team_os.infrastructure.database import MigrationRunner
from agent_team_os.live_evaluator_credentials import (
    KeychainEvaluatorCredentialStore,
    repair_evaluator_access,
)
from agent_team_os.modules.identity import IdentityService, SQLiteIdentityRepository
from agent_team_os.modules.identity.domain import BootstrapRequest, LoginRequest
from agent_team_os.shared.errors import ProductError


class FakeKeychainBackend:
    def __init__(self, password: str | None = None) -> None:
        self.password = password
        self.calls: list[tuple[str, str, str | None]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append((service, username, None))
        return self.password

    def set_password(self, service: str, username: str, password: str) -> None:
        self.calls.append((service, username, password))
        self.password = password


def test_keychain_password_is_stable_and_never_passed_as_an_argument() -> None:
    backend = FakeKeychainBackend()
    store = KeychainEvaluatorCredentialStore(backend=backend)

    first = store.get_or_create("v051-evaluator")
    second = store.get_or_create("v051-evaluator")

    assert first == second
    assert len(first) >= 32
    writes = [call for call in backend.calls if call[2] is not None]
    assert writes == [(store.service, "v051-evaluator", first)]


def test_repair_rotates_existing_evaluator_through_identity_service(tmp_path: Path) -> None:
    database = tmp_path / "identity.sqlite"
    MigrationRunner(database, Path(__file__).parents[1] / "migrations").migrate()
    identity = IdentityService(SQLiteIdentityRepository(database))
    user = identity.bootstrap(
        BootstrapRequest(
            username="v051-evaluator",
            display_name="Live Evaluator",
            password="OldPassword123!",
        )
    )
    store = KeychainEvaluatorCredentialStore(
        backend=FakeKeychainBackend("StablePassword456!")
    )

    repaired = repair_evaluator_access(
        database=database,
        username="v051-evaluator",
        store=store,
    )

    assert repaired.user_id == user.id
    assert repaired.user_version == 2
    identity.login(LoginRequest(username="v051-evaluator", password="StablePassword456!"))
    with pytest.raises(ProductError) as error:
        identity.login(LoginRequest(username="v051-evaluator", password="OldPassword123!"))
    assert error.value.code == "IDENTITY_LOGIN_FAILED"
