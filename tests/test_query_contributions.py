"""完整贡献身份去重；不跳过执行，也不按相似文本合并。"""
from agent_team_os.modules.knowledge.index_application import _merge_query_hits
from agent_team_os.modules.knowledge.index_domain import (
    KnowledgeRetrievalHit,
    RetrievalPolicyRevision,
)
from agent_team_os.modules.knowledge.query_fusion import contribution_audit, contribution_groups


def hit(chunk: str, content: str = "a") -> KnowledgeRetrievalHit:
    return KnowledgeRetrievalHit.model_construct(
        source_id="source", snapshot_id="snapshot", chunk_id=chunk,
        content_sha256=content * 64, citation_id=chunk, content=content,
    )


def test_exact_complete_contributions_are_idempotent() -> None:
    a, b = hit("a"), hit("b")
    groups = contribution_groups(((a, b), (a, b), (a,), (b, a), (hit("a", "b"), b)))
    assert [item[0] for item in groups] == [0, 0, 2, 3, 4]
    assert groups[0][1] == groups[1][1]
    assert len({item[1] for item in groups}) == 4


def test_empty_and_repeated_rank_members_are_not_partial_overlap() -> None:
    a = hit("a")
    assert [item[0] for item in contribution_groups(((), (), (a,), (a, a)))] == [0, 0, 2, 3]


def test_duplicate_rankings_preserve_scores_and_audit_every_unit() -> None:
    a, b = hit("a"), hit("b")
    policy = RetrievalPolicyRevision.model_construct(
        score_precision=8, min_score=0, top_k=10, max_context_bytes=8192,
    )
    once = _merge_query_hits(((a, b),), policy)
    repeated = _merge_query_hits(((a, b),) * 4, policy)
    assert repeated == once
    partial = _merge_query_hits(((a, b), (a,)), policy)
    assert partial[0].score.rrf_score > once[0].score.rrf_score
    hashes = tuple(str(i) * 64 for i in range(4))
    audit = contribution_audit(((a, b),) * 4, hashes)
    assert len(audit) == 4
    assert [row["unit_ordinal"] for row in audit] == list(range(4))
    assert [row["input_sha256"] for row in audit] == list(hashes)
    assert all(row["representative_ordinal"] == 0 for row in audit)
