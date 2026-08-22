# ADR-0005: Evidence Is an Immutable, Verifiable Product Fact

Status: accepted for V0.2.1.

## Decision

An Evidence Record is append-only and contains its kind, source reference, producing identity,
content hash, creation time, and verification status. Verification appends a new verification
result; it never overwrites the original fact.

Only a 64-character non-zero SHA-256 whose source can be re-read may be `verified`. Missing source,
all-zero hashes, changed content, absent Git objects, and incomplete legacy snapshots are `invalid`
or `unavailable`. UI success colour is derived from verification status, not Delivery status.

Deterministic evidence is allowed only in deterministic gates and remains visibly identified as
such. It cannot satisfy a Live release gate.

