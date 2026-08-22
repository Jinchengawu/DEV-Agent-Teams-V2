from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Sha256(str):
    @classmethod
    def validate(cls, value: str) -> Sha256:
        if not _SHA256.fullmatch(value) or value == "0" * 64:
            raise ValueError("SHA-256 must be lowercase, 64 characters, and non-zero")
        return cls(value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(cls.validate, core_schema.str_schema())


def sha256_bytes(value: bytes) -> Sha256:
    return Sha256.validate(hashlib.sha256(value).hexdigest())


def sha256_json(value: object) -> Sha256:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def is_valid_sha256(value: str | None) -> bool:
    return bool(value and _SHA256.fullmatch(value) and value != "0" * 64)

