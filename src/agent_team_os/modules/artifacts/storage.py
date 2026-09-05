from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared.hashes import Sha256, sha256_bytes


class ArtifactStorageError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ArtifactReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str
    sha256: Sha256
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def uri_matches_digest(self) -> ArtifactReference:
        if self.uri != f"artifact://sha256/{self.sha256}":
            raise ValueError("Artifact URI must be derived from its SHA-256")
        return self


class ContentAddressedArtifactStorage:
    """Store immutable Artifact bytes behind one content-addressed interface."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        (self.root / "sha256").mkdir(parents=True, exist_ok=True)

    def put_json(
        self,
        content: object,
        *,
        media_type: str = "application/json",
    ) -> ArtifactReference:
        payload = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(payload, media_type=media_type)

    def put_bytes(self, payload: bytes, *, media_type: str) -> ArtifactReference:
        digest = sha256_bytes(payload)
        reference = ArtifactReference(
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            media_type=media_type,
            size_bytes=len(payload),
        )
        target = self.path_for(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_bytes(reference, target.read_bytes())
            return reference
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return reference

    def get_json(self, reference: ArtifactReference) -> object:
        try:
            return json.loads(self.get_bytes(reference))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStorageError(
                "ARTIFACT_JSON_INVALID", "Artifact bytes are not valid UTF-8 JSON"
            ) from error

    def get_bytes(self, reference: ArtifactReference, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and (max_bytes < 0 or reference.size_bytes > max_bytes):
            raise ArtifactStorageError("ARTIFACT_SIZE_LIMIT", "Artifact 超过读取大小限制")
        target = self.path_for(reference)
        try:
            with target.open("rb") as stream:
                payload = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        except FileNotFoundError as error:
            raise ArtifactStorageError(
                "ARTIFACT_OBJECT_MISSING", f"object {reference.sha256} is unavailable"
            ) from error
        if max_bytes is not None and len(payload) > max_bytes:
            raise ArtifactStorageError("ARTIFACT_SIZE_LIMIT", "Artifact 实际内容超过读取限制")
        self._verify_bytes(reference, payload)
        return payload

    def path_for(self, reference: ArtifactReference) -> Path:
        expected_uri = f"artifact://sha256/{reference.sha256}"
        if reference.uri != expected_uri:
            raise ArtifactStorageError(
                "ARTIFACT_REFERENCE_INVALID", "Artifact URI and SHA-256 disagree"
            )
        return self.root / "sha256" / str(reference.sha256)[:2] / str(reference.sha256)

    @staticmethod
    def _verify_bytes(reference: ArtifactReference, payload: bytes) -> None:
        if len(payload) != reference.size_bytes or sha256_bytes(payload) != reference.sha256:
            raise ArtifactStorageError(
                "ARTIFACT_CONTENT_HASH_MISMATCH",
                f"stored object {reference.sha256} failed integrity verification",
            )
