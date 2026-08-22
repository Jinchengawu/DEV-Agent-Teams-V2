from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMINISTRATOR = "administrator"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(StrEnum):
    DELIVERY_CREATE = "delivery:create"
    PLAN_DECIDE = "delivery:plan-decide"
    CANDIDATE_APPLY = "delivery:candidate-apply"
    JOURNEY_PUBLISH = "journey:publish"
    JOURNEY_EDIT = "journey:edit"
    AGENT_MANAGE = "agents:manage"
    EVIDENCE_VERIFY = "evidence:verify"
    WIKI_EDIT = "wiki:edit"
    SETTINGS_EDIT = "settings:edit"
    USER_MANAGE = "users:manage"
    WORKSPACE_RESET = "workspace:reset"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMINISTRATOR: frozenset(Permission),
    Role.EDITOR: frozenset(
        {
            Permission.DELIVERY_CREATE,
            Permission.PLAN_DECIDE,
            Permission.JOURNEY_EDIT,
            Permission.WIKI_EDIT,
        }
    ),
    Role.VIEWER: frozenset(),
}


def permits(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
