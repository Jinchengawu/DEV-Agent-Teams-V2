from __future__ import annotations

from agent_team_os.infrastructure.git import ExternalGitBinding
from agent_team_os.infrastructure.github import GitHubPullRequestProvider
from agent_team_os.modules.releases import WorkspaceCandidateV2
from agent_team_os.shared.errors import ProductError


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


class FlakyTransport(Transport):
    def __init__(self, *, error_code: str) -> None:
        super().__init__()
        self.error_code = error_code
        self.gets = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        if method == "GET":
            self.gets += 1
            if self.gets == 1:
                raise ProductError(
                    code=self.error_code,
                    title="GitHub PR Provider 操作失败",
                    detail="simulated provider failure",
                    repair="retry only when transient",
                )
        return super().request(method, url, token=token, payload=payload)


def test_github_pr_retries_one_transient_provider_failure(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GITHUB_TOKEN", "session-only-token")
    candidate = _candidate()
    transport = FlakyTransport(error_code="GITHUB_PR_PROVIDER_TRANSIENT")

    receipt = GitHubPullRequestProvider(transport).ensure(candidate, _binding(candidate))

    assert receipt.pull_request_id == 17
    assert transport.gets == 2
    assert transport.posts == 1


def test_github_pr_does_not_retry_permanent_provider_failure(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GITHUB_TOKEN", "session-only-token")
    candidate = _candidate()
    transport = FlakyTransport(error_code="GITHUB_PR_PROVIDER_REJECTED")

    try:
        GitHubPullRequestProvider(transport).ensure(candidate, _binding(candidate))
    except ProductError as error:
        assert error.code == "GITHUB_PR_PROVIDER_REJECTED"
    else:
        raise AssertionError("permanent provider error must fail closed")

    assert transport.gets == 1
    assert transport.posts == 0


def _candidate() -> WorkspaceCandidateV2:
    return WorkspaceCandidateV2(
        delivery_id="delivery-retry",
        project_id="project-1",
        workcell_key="design",
        workspace_binding_id="workspace-1",
        repository_uri="https://github.com/example/frontend.git",
        adapter_type="external-git",
        base_revision="1" * 40,
        candidate_revision="2" * 40,
        diff_sha256="3" * 64,
        candidate_branch="agent-team-os/delivery-retry/design",
        verification_sha256="4" * 64,
        review_artifact_ids=("review-1",),
        evidence_sha256="5" * 64,
    )


def _binding(candidate: WorkspaceCandidateV2) -> ExternalGitBinding:
    return ExternalGitBinding(
        remote_uri=candidate.repository_uri,
        credential_reference="env://TEST_GITHUB_TOKEN",
    )
