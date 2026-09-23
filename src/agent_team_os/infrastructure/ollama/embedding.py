from __future__ import annotations

from collections.abc import Mapping

import httpx

from ...modules.knowledge.index_domain import EmbeddingModelDescriptor
from ...modules.knowledge.index_ports import EmbeddingFailure


class OllamaEmbeddingAdapter:
    adapter_revision = "ollama-embedding-http-v1"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            base_url="http://127.0.0.1:11434",
            timeout=httpx.Timeout(60),
            trust_env=False,
        )

    def describe(self, model_name: str) -> EmbeddingModelDescriptor:
        payload = self._get_json("/api/tags")
        models = payload.get("models")
        if not isinstance(models, list):
            raise EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_RESPONSE_INVALID",
                "Ollama tags response has no models list",
            )
        for model in models:
            if not isinstance(model, Mapping):
                continue
            name = model.get("name") or model.get("model")
            if name != model_name:
                continue
            digest = model.get("digest")
            if not isinstance(digest, str) or not digest:
                raise EmbeddingFailure(
                    "KNOWLEDGE_OLLAMA_MODEL_DIGEST_MISSING",
                    "Ollama model has no immutable digest",
                )
            return EmbeddingModelDescriptor(
                model_name=model_name,
                model_digest=digest,
            )
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_MODEL_MISSING",
            "Required Ollama model is not installed; automatic pull is disabled",
        )

    def embed(
        self,
        texts: tuple[str, ...],
        *,
        model_name: str,
        truncate: bool,
    ) -> tuple[tuple[float, ...], ...]:
        if truncate:
            raise EmbeddingFailure(
                "KNOWLEDGE_EMBEDDING_TRUNCATION_FORBIDDEN",
                "Knowledge embedding must use truncate=false",
            )
        payload = self._post_json(
            "/api/embed",
            {
                "model": model_name,
                "input": list(texts),
                "truncate": False,
            },
        )
        raw_vectors = payload.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(texts):
            raise EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_RESPONSE_INVALID",
                "Ollama embed response does not match input count",
            )
        vectors: list[tuple[float, ...]] = []
        for raw_vector in raw_vectors:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise EmbeddingFailure(
                    "KNOWLEDGE_OLLAMA_RESPONSE_INVALID",
                    "Ollama returned an empty embedding",
                )
            if any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in raw_vector
            ):
                raise EmbeddingFailure(
                    "KNOWLEDGE_OLLAMA_RESPONSE_INVALID",
                    "Ollama embedding contains a non-numeric value",
                )
            vectors.append(tuple(float(value) for value in raw_vector))
        return tuple(vectors)

    def _get_json(self, path: str) -> Mapping[str, object]:
        failure = None
        try:
            response = self.client.get(path)
        except httpx.HTTPError as error:
            failure = EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_UNAVAILABLE", "Ollama request failed",
                category="timeout" if isinstance(error, httpx.TimeoutException) else "transport",
            )
        if failure is not None:
            raise failure
        return _decode(response)

    def _post_json(self, path: str, body: object) -> Mapping[str, object]:
        failure = None
        try:
            response = self.client.post(path, json=body)
        except httpx.HTTPError as error:
            failure = EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_UNAVAILABLE", "Ollama request failed",
                category="timeout" if isinstance(error, httpx.TimeoutException) else "transport",
            )
        if failure is not None:
            raise failure
        return _decode(response)


def _decode(response: httpx.Response) -> Mapping[str, object]:
    if response.status_code >= 400:
        # 只分类有界错误片段；正文不跨 Adapter、不进入异常链或诊断持久化。
        fragment = response.content[:4096].decode("utf-8", errors="replace").lower()
        category = "invalid_request"
        if response.status_code == 429:
            category = "rate_limited"
        elif response.status_code >= 500:
            category = "provider_error"
        elif any(word in fragment for word in ("context", "token", "input length")) and any(
            word in fragment for word in ("exceed", "too long", "maximum", "too large")
        ):
            category = "input_limit"
        del fragment
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_REQUEST_FAILED",
            f"Ollama returned HTTP {response.status_code}",
            http_status=response.status_code,
            category=category,  # type: ignore[arg-type]
        )
    invalid_json = False
    try:
        payload = response.json()
    except ValueError:
        invalid_json = True
        payload = None
    if invalid_json:
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_RESPONSE_INVALID", "Ollama returned invalid JSON",
            category="invalid_response",
        )
    if not isinstance(payload, Mapping):
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_RESPONSE_INVALID", "Ollama response is not an object",
            category="invalid_response",
        )
    return payload
