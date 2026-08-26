from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from ...git_sandbox import GitSandbox
from ...shared.repositories import RepositoryRole

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REPOSITORY_ROLES = frozenset({"backend", "design", "frontend", "qa"})


class ProjectGitWorkspaces:
    """Resolve product-owned workspace references to isolated Git sandboxes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def provision(self, repository_ref: str) -> str:
        sandbox = self._for_repository_ref(repository_ref)
        sandbox.ensure_initialized()
        return sandbox.main_revision()

    def reset(self, repository_ref: str) -> str:
        return self._for_repository_ref(repository_ref).reset()

    def revision(self, repository_ref: str) -> str:
        return self._for_repository_ref(repository_ref).main_revision()

    def for_workspace(self, workspace_id: str) -> GitSandbox:
        if workspace_id == "backend-demo":
            return GitSandbox(self.root, "backend")
        prefix = "project:"
        if not workspace_id.startswith(prefix):
            raise ValueError("unknown product workspace reference")
        identity = workspace_id.removeprefix(prefix)
        project_id, separator, role = identity.partition(":")
        if not _PROJECT_ID.fullmatch(project_id):
            raise ValueError("invalid product workspace reference")
        if not separator:
            return GitSandbox(self.root / "projects" / project_id, "backend")
        if role not in _REPOSITORY_ROLES:
            raise ValueError("invalid product workspace reference")
        return GitSandbox(
            self.root / "projects" / project_id / role,
            cast(RepositoryRole, role),
        )

    def _for_repository_ref(self, repository_ref: str) -> GitSandbox:
        if repository_ref == "legacy/backend-demo":
            return GitSandbox(self.root, "backend")
        prefix = "projects/"
        if not repository_ref.startswith(prefix):
            raise ValueError("unknown repository reference")
        identity = repository_ref.removeprefix(prefix)
        parts = identity.split("/")
        if len(parts) not in {1, 2} or not _PROJECT_ID.fullmatch(parts[0]):
            raise ValueError("invalid repository reference")
        if len(parts) == 1:
            return GitSandbox(self.root / "projects" / parts[0], "backend")
        if parts[1] not in _REPOSITORY_ROLES:
            raise ValueError("invalid repository reference")
        return GitSandbox(
            self.root / "projects" / parts[0] / parts[1],
            cast(RepositoryRole, parts[1]),
        )
