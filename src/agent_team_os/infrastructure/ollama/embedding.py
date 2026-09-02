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
        try:
            response = self.client.get(path)
        except httpx.HTTPError as error:
            raise EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_UNAVAILABLE", "Ollama request failed"
            ) from error
        return _decode(response)

    def _post_json(self, path: str, body: object) -> Mapping[str, object]:
        try:
            response = self.client.post(path, json=body)
        except httpx.HTTPError as error:
            raise EmbeddingFailure(
                "KNOWLEDGE_OLLAMA_UNAVAILABLE", "Ollama request failed"
            ) from error
        return _decode(response)


def _decode(response: httpx.Response) -> Mapping[str, object]:
    if response.status_code >= 400:
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_REQUEST_FAILED",
            f"Ollama returned HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_RESPONSE_INVALID", "Ollama returned invalid JSON"
        ) from error
    if not isinstance(payload, Mapping):
        raise EmbeddingFailure(
            "KNOWLEDGE_OLLAMA_RESPONSE_INVALID", "Ollama response is not an object"
        )
    return payload
