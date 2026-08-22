# ADR-0004: Stable Problems and Generated Web Contracts

Status: accepted for V0.2.1.

## Decision

Existing `/v1` success payloads remain compatible. Failures use `application/problem+json` with a
stable `code`, Chinese `title`, actionable `detail` and `repair`, `trace_id`, and optional optimistic
version fields. Web code never displays raw Python exceptions.

FastAPI OpenAPI is the authoritative web contract. TypeScript request/response types are generated
from it and checked for drift in CI. Handwritten view models may extend generated data but cannot
redefine transport objects.

