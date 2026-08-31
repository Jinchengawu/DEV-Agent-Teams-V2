from __future__ import annotations

from agent_team_os.infrastructure.git import ExternalGitBinding
from agent_team_os.infrastructure.github import GitHubPullRequestProvider
from agent_team_os.modules.releases import WorkspaceCandidateV2


class Transport:
    def __init__(self) -> None:
        self.pull: dict[str, object] | None = None
        self.posts = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        assert token == "session-only-token"
        if method == "GET":
            return [] if self.pull is None else [self.pull]
        assert method == "POST"
        assert payload is not None
        self.posts += 1
        self.pull = {
            "number": 17,
            "html_url": "https://github.com/example/frontend/pull/17",
            "state": "open",
            "draft": False,
            "merged_at": None,
            "head": {"ref": payload["head"], "sha": "2" * 40},
            "base": {"ref": payload["base"]},
        }
        return self.pull


def test_github_pr_is_idempotent_review_surface_and_never_merges(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GITHUB_TOKEN", "session-only-token")
    candidate = WorkspaceCandidateV2(
        delivery_id="delivery-1",
        project_id="project-1",
        workcell_key="frontend",
        workspace_binding_id="workspace-1",
        repository_uri="https://github.com/example/frontend.git",
        adapter_type="external-git",
        base_revision="1" * 40,
        candidate_revision="2" * 40,
        diff_sha256="3" * 64,
        candidate_branch="agent-team-os/delivery-1/frontend",
        verification_sha256="4" * 64,
        review_artifact_ids=("review-1",),
        evidence_sha256="5" * 64,
    )
    binding = ExternalGitBinding(
        remote_uri=candidate.repository_uri,
        credential_reference="env://TEST_GITHUB_TOKEN",
    )
    transport = Transport()
    provider = GitHubPullRequestProvider(transport)

    first = provider.ensure(candidate, binding)
    second = provider.ensure(candidate, binding)

    assert first == second
    assert first.pull_request_id == 17
    assert first.head_candidate_sha == candidate.candidate_revision
    assert first.base_branch == "main"
    assert first.state == "open"
    assert transport.posts == 1
