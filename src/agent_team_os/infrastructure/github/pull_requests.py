from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

from ...infrastructure.git import ExternalGitBinding, resolve_git_credential
from ...modules.releases import GitHubPRReceiptCreate, WorkspaceCandidateV2
from ...shared.errors import ProductError


class GitHubTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        payload: dict[str, object] | None = None,
    ) -> object: ...


class GitHubPullRequestProvider:
    """Create or refresh a PR review surface without acquiring merge authority."""

    def __init__(self, transport: GitHubTransport | None = None) -> None:
        self.transport = transport or _UrllibGitHubTransport()

    def ensure(
        self,
        candidate: WorkspaceCandidateV2,
        binding: ExternalGitBinding,
    ) -> GitHubPRReceiptCreate:
        owner, repository = _github_repository(binding)
        if binding.credential_reference is None:
            raise _github_error(
                "GITHUB_CREDENTIAL_REQUIRED",
                "GitHub PR Provider 需要环境变量或 Keychain Credential Reference。",
            )
        token = resolve_git_credential(binding.credential_reference)
        api_root = f"https://api.github.com/repos/{owner}/{repository}"
        query = urllib.parse.urlencode(
            {
                "state": "all",
                "head": f"{owner}:{candidate.candidate_branch}",
                "base": "main",
                "per_page": 100,
            }
        )
        found = self.transport.request(
            "GET",
            f"{api_root}/pulls?{query}",
            token=token,
        )
        pull = _matching_pull(found, candidate)
        if pull is None:
            created = self.transport.request(
                "POST",
                f"{api_root}/pulls",
                token=token,
                payload={
                    "title": (
                        f"Agent-Team-OS {candidate.delivery_id} / {candidate.workcell_key}"
                    ),
                    "body": (
                        "由 Agent-Team-OS 创建的审查界面。Apply 权威仍属于产品 Release Gate，"
                        "不会调用 GitHub Merge。"
                    ),
                    "head": candidate.candidate_branch,
                    "base": "main",
                    "draft": False,
                },
            )
            if not isinstance(created, dict):
                raise _github_error(
                    "GITHUB_PR_RESPONSE_INVALID",
                    "GitHub Create Pull Request 返回了无效结构。",
                )
            pull = cast(dict[str, object], created)
        return _receipt(candidate, pull)


class _UrllibGitHubTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Agent-Team-OS/0.5",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise _github_error(
                "GITHUB_PR_PROVIDER_FAILED",
                "GitHub PR API 调用失败或返回无效 JSON。",
            ) from error


def _github_repository(binding: ExternalGitBinding) -> tuple[str, str]:
    parsed = urlparse(binding.remote_uri)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise _github_error(
            "GITHUB_REMOTE_REQUIRED",
            "PR Provider 只接受 GitHub HTTPS Workspace Binding。",
        )
    parts = parsed.path.removesuffix(".git").strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise _github_error(
            "GITHUB_REMOTE_INVALID",
            "GitHub Repository 地址必须是 owner/repository。",
        )
    return parts[0], parts[1]


def _matching_pull(
    response: object,
    candidate: WorkspaceCandidateV2,
) -> dict[str, object] | None:
    if not isinstance(response, list):
        raise _github_error(
            "GITHUB_PR_RESPONSE_INVALID",
            "GitHub Pull Request 查询返回了无效结构。",
        )
    for item in response:
        if not isinstance(item, dict):
            continue
        head = item.get("head")
        base = item.get("base")
        if (
            isinstance(head, dict)
            and isinstance(base, dict)
            and head.get("ref") == candidate.candidate_branch
            and head.get("sha") == candidate.candidate_revision
            and base.get("ref") == "main"
        ):
            return cast(dict[str, object], item)
    return None


def _receipt(
    candidate: WorkspaceCandidateV2,
    pull: dict[str, object],
) -> GitHubPRReceiptCreate:
    number = pull.get("number")
    url = pull.get("html_url")
    head = pull.get("head")
    base = pull.get("base")
    if (
        not isinstance(number, int)
        or not isinstance(url, str)
        or not isinstance(head, dict)
        or not isinstance(base, dict)
        or head.get("ref") != candidate.candidate_branch
        or head.get("sha") != candidate.candidate_revision
        or base.get("ref") != "main"
    ):
        raise _github_error(
            "GITHUB_PR_CANDIDATE_MISMATCH",
            "GitHub PR 回执没有绑定冻结 Candidate。",
        )
    state = pull.get("state")
    if pull.get("merged_at") is not None:
        normalized: Literal["open", "draft", "closed", "merged"] = "merged"
    elif state == "closed":
        normalized = "closed"
    elif pull.get("draft") is True:
        normalized = "draft"
    else:
        normalized = "open"
    return GitHubPRReceiptCreate(
        pull_request_id=number,
        url=url,
        head_branch=candidate.candidate_branch,
        head_candidate_sha=candidate.candidate_revision,
        state=normalized,
    )


def _github_error(code: str, detail: str) -> ProductError:
    return ProductError(
        code=code,
        title="GitHub PR Provider 操作失败",
        detail=detail,
        repair="检查 GitHub Repository、Token Scope 与 Candidate Branch 后重试。",
    )
