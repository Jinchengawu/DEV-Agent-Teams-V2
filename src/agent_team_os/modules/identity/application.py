from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from ...shared.clock import Clock, SystemClock
from ...shared.errors import ProductError
from ...shared.ids import new_id
from ...shared.permissions import Permission, Role, permits
from .domain import BootstrapRequest, LoginRequest, SessionGrant, User, UserCreate, UserPatch
from .ports import IdentityRepository, UserUpdateResult

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SESSION_TTL = timedelta(hours=12)


class IdentityService:
    def __init__(self, repository: IdentityRepository, clock: Clock | None = None) -> None:
        self.repository = repository
        self.clock = clock or SystemClock()

    def bootstrap_required(self) -> bool:
        return self.repository.count_users() == 0

    def bootstrap(self, request: BootstrapRequest) -> User:
        if not self.bootstrap_required():
            raise ProductError(
                code="IDENTITY_ALREADY_BOOTSTRAPPED",
                title="系统已完成初始化",
                detail="管理员账户已存在，不能再次执行首次初始化。",
                repair="使用已有管理员账户登录。",
            )
        create = UserCreate(
            username=request.username,
            display_name=request.display_name,
            role=Role.ADMINISTRATOR,
            password=request.password,
        )
        return self._create(create)

    def login(self, request: LoginRequest) -> SessionGrant:
        found = self.repository.get_user_by_username(request.username)
        if found is None or not found[0].enabled or not verify_password(
            request.password, found[1]
        ):
            raise ProductError(
                code="IDENTITY_LOGIN_FAILED",
                title="登录失败",
                detail="用户名或密码不正确，或账户已被停用。",
                repair="检查登录信息，或联系管理员恢复账户。",
                status_code=401,
            )
        user = found[0]
        now = self.clock.now()
        expires_at = now + SESSION_TTL
        bearer = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self.repository.create_session(
            new_id(),
            user.id,
            _token_hash(bearer),
            _token_hash(csrf_token),
            expires_at,
            now,
        )
        return SessionGrant(
            user=user,
            bearer=bearer,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def authenticate(self, bearer: str | None) -> User:
        if not bearer:
            raise self.authentication_required()
        resolved = self.repository.resolve_session(_token_hash(bearer), self.clock.now())
        if resolved is None:
            raise self.authentication_required()
        return resolved[0]

    def authenticate_mutation(self, bearer: str | None, csrf_token: str | None) -> User:
        if not bearer:
            raise self.authentication_required()
        resolved = self.repository.resolve_session(_token_hash(bearer), self.clock.now())
        if resolved is None:
            raise self.authentication_required()
        if not csrf_token or not hmac.compare_digest(resolved[1], _token_hash(csrf_token)):
            raise ProductError(
                code="IDENTITY_CSRF_REJECTED",
                title="请求安全校验失败",
                detail="请求缺少有效的 CSRF 令牌。",
                repair="刷新页面后重试；若仍失败，请重新登录。",
                status_code=403,
            )
        return resolved[0]

    def logout(self, bearer: str | None) -> None:
        if bearer:
            self.repository.revoke_session(_token_hash(bearer))

    def list_users(self, actor: User) -> tuple[User, ...]:
        self.require(actor, Permission.USER_MANAGE)
        return self.repository.list_users()

    def create_user(self, actor: User, request: UserCreate) -> User:
        self.require(actor, Permission.USER_MANAGE)
        return self._create(request)

    def patch_user(self, actor: User, user_id: str, request: UserPatch) -> User:
        self.require(actor, Permission.USER_MANAGE)
        current = self.repository.get_user(user_id)
        if current is None:
            raise ProductError(
                code="IDENTITY_USER_NOT_FOUND",
                title="用户不存在",
                detail="要修改的用户已不存在。",
                repair="刷新用户列表后重试。",
                status_code=404,
            )
        if current.version != request.expected_version:
            raise ProductError(
                code="IDENTITY_USER_VERSION_CONFLICT",
                title="用户版本冲突",
                detail="用户资料已被其他操作更新。",
                repair="刷新用户详情后重新保存。",
                expected_version=request.expected_version,
                actual_version=current.version,
            )
        next_role = request.role or current.role
        next_enabled = current.enabled if request.enabled is None else request.enabled
        password_hash = None
        if request.password is not None:
            _validate_password(request.password)
            password_hash = hash_password(request.password)
        now = self.clock.now()
        updated = current.model_copy(
            update={
                "display_name": request.display_name or current.display_name,
                "role": next_role,
                "enabled": next_enabled,
                "version": current.version + 1,
                "updated_at": now,
            }
        )
        update_result = self.repository.compare_and_swap_user(
            request.expected_version, updated, password_hash
        )
        if update_result == UserUpdateResult.LAST_ADMIN_REQUIRED:
            raise ProductError(
                code="IDENTITY_LAST_ADMIN_REQUIRED",
                title="必须保留管理员",
                detail="不能停用或降级最后一个启用的管理员。",
                repair="先创建或启用另一个管理员账户。",
            )
        if update_result == UserUpdateResult.VERSION_CONFLICT:
            latest = self.repository.get_user(user_id)
            raise ProductError(
                code="IDENTITY_USER_VERSION_CONFLICT",
                title="用户版本冲突",
                detail="用户资料在保存期间已被更新。",
                repair="刷新用户详情后重新保存。",
                expected_version=request.expected_version,
                actual_version=None if latest is None else latest.version,
            )
        return updated

    @staticmethod
    def require(actor: User, permission: Permission) -> None:
        if not permits(actor.role, permission):
            raise ProductError(
                code="IDENTITY_PERMISSION_DENIED",
                title="权限不足",
                detail="当前账户无权执行该操作。",
                repair="联系管理员调整角色，或使用具备权限的账户。",
                status_code=403,
            )

    def _create(self, request: UserCreate) -> User:
        if self.repository.get_user_by_username(request.username) is not None:
            raise ProductError(
                code="IDENTITY_USERNAME_CONFLICT",
                title="用户名已存在",
                detail="该用户名已被占用。",
                repair="使用其他用户名重试。",
            )
        now = self.clock.now()
        user = User(
            id=new_id(),
            username=request.username,
            display_name=request.display_name,
            role=request.role,
            enabled=True,
            version=1,
            created_at=now,
            updated_at=now,
        )
        return self.repository.create_user(user, hash_password(request.password))

    @staticmethod
    def authentication_required() -> ProductError:
        return ProductError(
            code="IDENTITY_AUTHENTICATION_REQUIRED",
            title="需要登录",
            detail="当前请求没有有效的登录会话。",
            repair="请登录后重试。",
            status_code=401,
        )


def hash_password(password: str) -> str:
    _validate_password(password)
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, expected_value = encoded.split("$", 5)
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (
            SCRYPT_N,
            SCRYPT_R,
            SCRYPT_P,
        ):
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_password(password: str) -> None:
    if len(password) < 12 or not any(character.isalpha() for character in password) or not any(
        character.isdigit() for character in password
    ):
        raise ProductError(
            code="IDENTITY_PASSWORD_WEAK",
            title="密码强度不足",
            detail="密码至少 12 位，且必须同时包含字母和数字。",
            repair="使用更长且包含字母和数字的密码。",
            status_code=422,
        )
