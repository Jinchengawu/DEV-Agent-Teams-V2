"""内部贡献组身份；全部单元执行后才可用于幂等融合。"""
from ...shared.hashes import sha256_json
from .index_domain import KnowledgeRetrievalHit


def contribution_groups(
    units: tuple[tuple[KnowledgeRetrievalHit, ...], ...],
) -> tuple[tuple[int, str], ...]:
    """返回每个单元的最小代表 ordinal 与完整有序身份 hash。"""
    representatives: dict[tuple[tuple[str, ...], ...], int] = {}
    result: list[tuple[int, str]] = []
    for ordinal, hits in enumerate(units):
        identity = tuple((h.source_id, h.snapshot_id, h.chunk_id,
                          str(h.content_sha256), h.citation_id) for h in hits)
        representative = representatives.setdefault(identity, ordinal)
        result.append((representative, str(sha256_json(identity))))
    return tuple(result)


def contribution_audit(
    units: tuple[tuple[KnowledgeRetrievalHit, ...], ...], input_hashes: tuple[str, ...],
) -> list[dict[str, str | int]]:
    if len(units) != len(input_hashes):
        raise ValueError("KNOWLEDGE_QUERY_CONTRIBUTION_SHAPE_INVALID")
    return [dict(unit_ordinal=ordinal, input_sha256=input_hashes[ordinal],
                 contribution_group_sha256=group, representative_ordinal=representative,
                 representative_input_sha256=input_hashes[representative])
            for ordinal, (representative, group) in enumerate(contribution_groups(units))]
