from __future__ import annotations

import argparse
import ctypes
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .modules.identity import IdentityService, SQLiteIdentityRepository
from .modules.identity.application import verify_password
from .modules.identity.domain import LoginRequest, UserPatch
from .shared.permissions import Role

DEFAULT_KEYCHAIN_SERVICE = "agent-team-os.live-evaluator.v2"
ERR_SEC_ITEM_NOT_FOUND = -25300


class KeychainBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class MacOSKeychainBackend:
    """Use Security.framework so secrets never enter process arguments or stdout."""

    security: ctypes.CDLL
    core_foundation: ctypes.CDLL

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("LIVE_EVALUATOR_KEYCHAIN_REQUIRES_MACOS")
        self.security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        self.core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self.security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.core_foundation.CFRelease.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _encoded(service: str, username: str) -> tuple[bytes, bytes]:
        return service.encode("utf-8"), username.encode("utf-8")

    def _find_item(
        self, service: bytes, username: bytes
    ) -> tuple[int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]:
        password_data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        password_length = ctypes.c_uint32()
        status = int(
            self.security.SecKeychainFindGenericPassword(
                None,
                len(service),
                service,
                len(username),
                username,
                ctypes.byref(password_length),
                ctypes.byref(password_data),
                ctypes.byref(item),
            )
        )
        return status, item, password_data, password_length

    def get_password(self, service: str, username: str) -> str | None:
        service_bytes, username_bytes = self._encoded(service, username)
        status, item, password_data, password_length = self._find_item(
            service_bytes, username_bytes
        )
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != 0:
            raise RuntimeError(f"LIVE_EVALUATOR_KEYCHAIN_READ_FAILED:{status}")
        try:
            raw = ctypes.string_at(password_data, password_length.value)
            return raw.decode("utf-8")
        finally:
            self.security.SecKeychainItemFreeContent(None, password_data)
            if item:
                self.core_foundation.CFRelease(item)

    def set_password(self, service: str, username: str, password: str) -> None:
        service_bytes, username_bytes = self._encoded(service, username)
        password_bytes = password.encode("utf-8")
        status, item, password_data, _ = self._find_item(service_bytes, username_bytes)
        if status == 0:
            try:
                update_status = int(
                    self.security.SecKeychainItemModifyAttributesAndData(
                        item,
                        None,
                        len(password_bytes),
                        ctypes.c_char_p(password_bytes),
                    )
                )
            finally:
                self.security.SecKeychainItemFreeContent(None, password_data)
                if item:
                    self.core_foundation.CFRelease(item)
            if update_status != 0:
                raise RuntimeError(f"LIVE_EVALUATOR_KEYCHAIN_WRITE_FAILED:{update_status}")
            return
        if status != ERR_SEC_ITEM_NOT_FOUND:
            raise RuntimeError(f"LIVE_EVALUATOR_KEYCHAIN_READ_FAILED:{status}")
        add_status = int(
            self.security.SecKeychainAddGenericPassword(
                None,
                len(service_bytes),
                service_bytes,
                len(username_bytes),
                username_bytes,
                len(password_bytes),
                ctypes.c_char_p(password_bytes),
                None,
            )
        )
        if add_status != 0:
            raise RuntimeError(f"LIVE_EVALUATOR_KEYCHAIN_WRITE_FAILED:{add_status}")


class KeychainEvaluatorCredentialStore:
    """Store a local evaluator password independently from third-party tokens."""

    def __init__(
        self,
        *,
        service: str = DEFAULT_KEYCHAIN_SERVICE,
        backend: KeychainBackend | None = None,
    ) -> None:
        self.service = service
        self.backend = backend or MacOSKeychainBackend()

    def get(self, username: str) -> str | None:
        password = self.backend.get_password(self.service, username)
        if password is None:
            return None
        if not password:
            return None
        return password

    def get_or_create(self, username: str) -> str:
        existing = self.get(username)
        if existing is not None:
            return existing
        password = f"{secrets.token_urlsafe(32)}Aa1!"
        self.backend.set_password(self.service, username, password)
        return password


@dataclass(frozen=True)
class EvaluatorCredentialRepair:
    user_id: str
    user_version: int
    rotated: bool


def repair_evaluator_access(
    *,
    database: Path,
    username: str,
    store: KeychainEvaluatorCredentialStore | None = None,
) -> EvaluatorCredentialRepair:
    credential_store = store or KeychainEvaluatorCredentialStore()
    password = credential_store.get_or_create(username)
    repository = SQLiteIdentityRepository(database)
    found = repository.get_user_by_username(username)
    if found is None:
        raise RuntimeError("LIVE_EVALUATOR_USER_NOT_FOUND")
    user, password_hash = found
    if user.role != Role.ADMINISTRATOR or not user.enabled:
        raise RuntimeError("LIVE_EVALUATOR_ADMIN_REQUIRED")
    if verify_password(password, password_hash):
        IdentityService(repository).login(LoginRequest(username=username, password=password))
        return EvaluatorCredentialRepair(
            user_id=user.id,
            user_version=user.version,
            rotated=False,
        )

    updated = IdentityService(repository).patch_user(
        user,
        user.id,
        UserPatch(expected_version=user.version, password=password),
    )
    IdentityService(repository).login(LoginRequest(username=username, password=password))
    return EvaluatorCredentialRepair(
        user_id=updated.id,
        user_version=updated.version,
        rotated=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="修复本地 Live Evaluator 的稳定 Keychain 登录凭据。"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--username", default="v051-evaluator")
    parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    args = parser.parse_args(argv)
    result = repair_evaluator_access(
        database=args.database,
        username=args.username,
        store=KeychainEvaluatorCredentialStore(service=args.keychain_service),
    )
    action = "rotated" if result.rotated else "verified"
    print(f"evaluator={args.username} action={action} user_version={result.user_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
