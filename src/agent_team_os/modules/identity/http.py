from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict

from ...shared.errors import ProductError
from .application import IdentityService
from .domain import BootstrapRequest, LoginRequest, User, UserCreate, UserPatch

SESSION_COOKIE = "agent_team_os_session"
CSRF_COOKIE = "agent_team_os_csrf"
CSRF_HEADER = "X-CSRF-Token"


class BootstrapStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bootstrap_required: bool


def create_identity_router(service: IdentityService) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/auth/bootstrap-status", response_model=BootstrapStatus)
    def bootstrap_status() -> BootstrapStatus:
        return BootstrapStatus(bootstrap_required=service.bootstrap_required())

    @router.post("/auth/bootstrap", response_model=User, status_code=status.HTTP_201_CREATED)
    def bootstrap(request_body: BootstrapRequest, request: Request) -> User:
        ensure_same_origin(request)
        return service.bootstrap(request_body)

    @router.post("/auth/login", response_model=User)
    def login(request_body: LoginRequest, request: Request, response: Response) -> User:
        ensure_same_origin(request)
        grant = service.login(request_body)
        max_age = max(int((grant.expires_at - service.clock.now()).total_seconds()), 0)
        response.set_cookie(
            SESSION_COOKIE,
            grant.bearer,
            max_age=max_age,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            grant.csrf_token,
            max_age=max_age,
            httponly=False,
            secure=False,
            samesite="strict",
            path="/",
        )
        return grant.user

    @router.get("/auth/session", response_model=User)
    def get_session(
        bearer: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> User:
        return service.authenticate(bearer)

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        request: Request,
        response: Response,
        bearer: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> Response:
        ensure_same_origin(request)
        service.authenticate_mutation(bearer, csrf_token)
        service.logout(bearer)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.get("/users", response_model=list[User])
    def list_users(
        bearer: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> tuple[User, ...]:
        return service.list_users(service.authenticate(bearer))

    @router.post("/users", response_model=User, status_code=status.HTTP_201_CREATED)
    def create_user(
        request_body: UserCreate,
        request: Request,
        bearer: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> User:
        ensure_same_origin(request)
        actor = service.authenticate_mutation(bearer, csrf_token)
        return service.create_user(actor, request_body)

    @router.patch("/users/{user_id}", response_model=User)
    def patch_user(
        user_id: str,
        request_body: UserPatch,
        request: Request,
        bearer: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> User:
        ensure_same_origin(request)
        actor = service.authenticate_mutation(bearer, csrf_token)
        return service.patch_user(actor, user_id, request_body)

    return router


def ensure_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin != expected:
        raise ProductError(
            code="IDENTITY_ORIGIN_REJECTED",
            title="请求来源被拒绝",
            detail="修改请求必须来自当前 Agent-Team-OS 页面。",
            repair="从当前系统页面重新执行操作。",
            status_code=403,
        )
