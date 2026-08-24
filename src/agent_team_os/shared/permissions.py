from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMINISTRATOR = "administrator"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(StrEnum):
    PROJECT_MANAGE = "projects:manage"
    DELIVERY_CREATE = "delivery:create"
    PLAN_DECIDE = "delivery:plan-decide"
    CANDIDATE_APPLY = "delivery:candidate-apply"
    JOURNEY_PUBLISH = "journey:publish"
    JOURNEY_EDIT = "journey:edit"
    AGENT_MANAGE = "agents:manage"
    AGENT_PROFILE_EDIT = "agent-profile:edit"
    AGENT_PROFILE_PUBLISH = "agent-profile:publish"
    AGENT_INSTANCE_MANAGE = "agent-instance:manage"
    AGENT_DEPLOYMENT_MANAGE = "agent-deployment:manage"
    CAPABILITY_EDIT = "capability:edit"
    CAPABILITY_PUBLISH = "capability:publish"
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
            Permission.AGENT_PROFILE_EDIT,
            Permission.CAPABILITY_EDIT,
            Permission.WIKI_EDIT,
        }
    ),
    Role.VIEWER: frozenset(),
}


def permits(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
