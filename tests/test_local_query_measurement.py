"""资产/身份接线测试；TinyTokenizer 不是生产模型或真实容量证据。"""
from __future__ import annotations

import json

import pytest

from agent_team_os.infrastructure.knowledge.local_query_measurement import (
    load_local_query_measurement,
    local_query_measurement_from_environment,
)
from agent_team_os.modules.knowledge.query_plan import QueryBudget, QueryInputQualification
from agent_team_os.shared.hashes import sha256_bytes, sha256_json

H = sha256_bytes(b"fixture implementation")


class TinyTokenizer:
    implementation_sha256 = H
    normalization = "identity"
    special_token_contract = "fixture-bos-eos-v1"

    def count(self, text: str) -> int:
        # 精确的合成 tokenizer：固定 UTF-8 byte vocabulary，加 BOS/EOS。
        return len(text.encode("utf-8")) + 2


def _assets(tmp_path):
    vocabulary = b"fixture-byte-vocabulary"
    (tmp_path / "vocab.bin").write_bytes(vocabulary)
    capacity = {"model_digest": "fixture-model", "unit_tokens": 10, "unit_bytes": 8}
    receipt = {"base_qualification_sha256": str(H), "model_digest": "fixture-model",
               "tokenizer_implementation_sha256": str(H), "dataset_passed": True}
    raw_capacity = json.dumps(capacity).encode()
    raw_receipt = json.dumps(receipt).encode()
    (tmp_path / "capacity.json").write_bytes(raw_capacity)
    (tmp_path / "receipt.json").write_bytes(raw_receipt)
    budget = QueryBudget(unit_tokens=10, unit_bytes=8, max_units=10, total_tokens=100,
                        total_bytes=80, overlap_tokens=0, overlap_bytes=0,
                        max_duration_ms=1000, max_cache_bytes=10000)
    manifest = {
        "schema_version": "local-query-measurement-v1", "tokenizer_kind": "fixture-only",
        "implementation_sha256": str(H), "normalization": "identity",
        "special_token_contract": "fixture-bos-eos-v1", "tokenizer_contract": "fixture-v1",
        "model_digest": "fixture-model", "base_qualification_sha256": str(H),
        "embedding_adapter_revision": "fixture-embedding", "evidence_kind": "deterministic",
        "assets": {"vocab.bin": str(sha256_bytes(vocabulary)),
                   "capacity.json": str(sha256_bytes(raw_capacity)),
                   "receipt.json": str(sha256_bytes(raw_receipt))},
        "capacity_file": "capacity.json", "receipt_file": "receipt.json",
        "budget": budget.model_dump(mode="json"),
    }
    raw = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    return manifest, sha256_bytes(raw)


def test_default_loader_rejects_unsupported_tokenizer(tmp_path):
    _, digest = _assets(tmp_path)
    with pytest.raises(ValueError, match="KNOWLEDGE_QUERY_TOKENIZER_UNAVAILABLE"):
        load_local_query_measurement(tmp_path, manifest_sha256=digest)


def test_exact_fixture_identity_special_tokens_and_receipt_binding(tmp_path):
    manifest, digest = _assets(tmp_path)
    adapter = load_local_query_measurement(
        tmp_path, manifest_sha256=digest,
        factories={"fixture-only": lambda _manifest, _assets: TinyTokenizer()},
        trusted_evidence_sha256=frozenset(manifest["assets"][f] for f in (
            "capacity.json", "receipt.json")),
    )
    for text in ("ASCII", "中文", "🙂", "e\u0301", " " * 7, " " * 8, " " * 9):
        measured = adapter.measure(text)
        assert measured.tokens == len(text.encode()) + 2
        assert measured.utf8_bytes == len(text.encode())
    q = QueryInputQualification(
        status="qualified", evidence_kind="deterministic", base_qualification_sha256=H,
        model_digest="fixture-model", embedding_adapter_revision="fixture-embedding",
        measurement_identity_sha256=adapter.identity_sha256,
        tokenizer_assets_sha256=sha256_json(manifest["assets"]),
        tokenizer_contract="fixture-v1",
        capacity_evidence_sha256=manifest["assets"]["capacity.json"],
        qualification_receipt_sha256=manifest["assets"]["receipt.json"],
        budget=QueryBudget.model_validate(manifest["budget"]),
    )
    assert adapter.verify_qualification(q)
    assert not adapter.verify_qualification(q.model_copy(update={"evidence_kind": "live"}))
    (tmp_path / "vocab.bin").write_bytes(b"tampered")
    assert not adapter.verify_qualification(q)
    with pytest.raises(ValueError, match="KNOWLEDGE_QUERY_ASSET_INVALID"):
        adapter.measure("private text")


def test_untrusted_receipt_and_symlink_are_rejected(tmp_path):
    manifest, digest = _assets(tmp_path)
    factory = {"fixture-only": lambda _manifest, _assets: TinyTokenizer()}
    with pytest.raises(ValueError, match="KNOWLEDGE_QUERY_EVIDENCE_UNTRUSTED"):
        load_local_query_measurement(tmp_path, manifest_sha256=digest, factories=factory)
    (tmp_path / "vocab.bin").unlink()
    (tmp_path / "vocab.bin").symlink_to(tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="KNOWLEDGE_QUERY_ASSET_INVALID"):
        load_local_query_measurement(tmp_path, manifest_sha256=digest, factories=factory,
                                     trusted_evidence_sha256=frozenset(manifest["assets"].values()))


def test_dedicated_nonsecret_configuration_remains_blocked_without_locked_implementation(tmp_path):
    manifest, digest = _assets(tmp_path)
    config = {
        "AGENT_TEAM_OS_QUERY_MEASUREMENT_ROOT": str(tmp_path),
        "AGENT_TEAM_OS_QUERY_MEASUREMENT_MANIFEST_SHA256": str(digest),
        "AGENT_TEAM_OS_QUERY_MEASUREMENT_EVIDENCE_SHA256S": ",".join(manifest["assets"].values()),
    }
    assert local_query_measurement_from_environment(environ={}) is None
    assert local_query_measurement_from_environment(environ=config) is None
    adapter = local_query_measurement_from_environment(
        environ=config, factories={"fixture-only": lambda _m, _a: TinyTokenizer()},
    )
    assert adapter is not None and adapter.measure("abc").tokens == 5
    config["AGENT_TEAM_OS_QUERY_MEASUREMENT_MANIFEST_SHA256"] = "invalid"
    assert local_query_measurement_from_environment(environ=config) is None


@pytest.mark.parametrize("field,value", [
    ("implementation_sha256", sha256_bytes(b"wrong implementation")),
    ("normalization", "unexpected-normalizer"),
    ("special_token_contract", "unexpected-special-tokens"),
])
def test_frozen_implementation_contract_drift_is_rejected(tmp_path, field, value):
    manifest, digest = _assets(tmp_path)
    tokenizer = TinyTokenizer()
    setattr(tokenizer, field, value)
    with pytest.raises(ValueError, match="KNOWLEDGE_QUERY_TOKENIZER_IDENTITY_INVALID"):
        load_local_query_measurement(
            tmp_path, manifest_sha256=digest,
            factories={"fixture-only": lambda _m, _a: tokenizer},
            trusted_evidence_sha256=frozenset(manifest["assets"].values()),
        )
