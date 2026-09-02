from __future__ import annotations

from datetime import UTC, datetime

from ...shared.errors import ProductError
from ...shared.ids import new_id
from ...shared.permissions import Role
from .domain import ProjectAccessActor, ProjectAccessAudit, ProjectCapability, ProjectRole
from .ports import ProjectRepository

_ALL_CAPABILITIES = frozenset(ProjectCapability)
_GLOBAL_CAPABILITIES: dict[Role, frozenset[ProjectCapability]] = {
    Role.ADMINISTRATOR: _ALL_CAPABILITIES,
    Role.EDITOR: frozenset(
        {
            ProjectCapability.READ,
            ProjectCapability.EDIT,
            ProjectCapability.DELIVERY_CREATE,
            ProjectCapability.DELIVERY_DECIDE,
            ProjectCapability.MEMBERSHIP_MANAGE,
            ProjectCapability.SOURCE_USE,
            ProjectCapability.SOURCE_MANAGE,
        }
    ),
    Role.VIEWER: frozenset({ProjectCapability.READ}),
}
_PROJECT_CAPABILITIES: dict[ProjectRole, frozenset[ProjectCapability]] = {
    "owner": frozenset(
        {
            ProjectCapability.READ,
            ProjectCapability.EDIT,
            ProjectCapability.DELIVERY_CREATE,
            ProjectCapability.DELIVERY_DECIDE,
            ProjectCapability.MEMBERSHIP_MANAGE,
            ProjectCapability.SOURCE_USE,
            ProjectCapability.SOURCE_MANAGE,
        }
    ),
    "editor": frozenset(
        {
            ProjectCapability.READ,
            ProjectCapability.DELIVERY_CREATE,
            ProjectCapability.DELIVERY_DECIDE,
            ProjectCapability.SOURCE_USE,
        }
    ),
    "viewer": frozenset({ProjectCapability.READ}),
}


class ProjectAccessPolicy:
    """Single authorization boundary for every Project-scoped resource."""

    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def visible_project_ids(self, actor: ProjectAccessActor) -> frozenset[str] | None:
        if Role(actor.global_role) == Role.ADMINISTRATOR:
            return None
        return frozenset(self.repository.list_membership_project_ids(actor.user_id))

    def require(
        self,
        actor: ProjectAccessActor,
        project_id: str,
        capability: ProjectCapability,
        *,
        resource: str,
        reason: str,
    ) -> ProjectAccessAudit | None:
        global_role = Role(actor.global_role)
        membership = self.repository.get_membership(project_id, actor.user_id)
        if global_role == Role.ADMINISTRATOR:
            if membership is None or capability not in _PROJECT_CAPABILITIES[membership.role]:
                audit = ProjectAccessAudit(
                    id=new_id(),
                    actor_user_id=actor.user_id,
                    project_id=project_id,
                    capability=capability.value,
                    resource=resource,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
                self.repository.append_access_audit(audit)
                return audit
            return None
        if (
            capability not in _GLOBAL_CAPABILITIES[global_role]
            or membership is None
            or capability not in _PROJECT_CAPABILITIES[membership.role]
        ):
            raise ProductError(
                code="PROJECT_ACCESS_DENIED",
                title="项目访问被拒绝",
                detail="当前用户没有访问该项目资源所需的全局与项目角色交集权限。",
                repair="联系项目 Owner 调整 Membership，或使用有权限的项目。",
                status_code=403,
            )
        return None
