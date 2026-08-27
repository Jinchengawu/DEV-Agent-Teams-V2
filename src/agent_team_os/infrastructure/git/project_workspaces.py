from __future__ import annotations

from pathlib import Path

from ...git_sandbox import GitSandbox


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

    def for_workspace(self, workspace_id: str) -> GitSandbox:
        if workspace_id == "backend-demo":
            return GitSandbox(self.root)
        prefix = "project:"
        if not workspace_id.startswith(prefix):
            raise ValueError("unknown product workspace reference")
        project_id = workspace_id.removeprefix(prefix)
        if not project_id or "/" in project_id or ".." in project_id:
            raise ValueError("invalid product workspace reference")
        return GitSandbox(self.root / "projects" / project_id)
    def _for_repository_ref(self, repository_ref: str) -> GitSandbox:
        if repository_ref == "legacy/backend-demo":
            return GitSandbox(self.root)
        prefix = "projects/"
        if not repository_ref.startswith(prefix):
            raise ValueError("unknown repository reference")
        project_id = repository_ref.removeprefix(prefix)
        if not project_id or "/" in project_id or ".." in project_id:
            raise ValueError("invalid repository reference")
        return GitSandbox(self.root / "projects" / project_id)
