# ADR-0003: One SQLite Commit for Product State and Events

Status: accepted for V0.2.1.

## Decision

New installations use `.agent-team-os/agent-team-os.sqlite`. A checksummed Migration Runner owns
schema evolution. Command Handlers use one UnitOfWork to commit Aggregate state, Evidence Records,
and Product Events. SSE and rebuildable projections read only committed Product Events.

SQLite enables foreign keys, WAL, and a busy timeout on every connection. Migrations execute one at
a time in transactions and refuse to continue when an applied migration checksum changes.

Legacy databases are copied before import. Invalid active Delivery snapshots are preserved in an
audit table and become `failed/LEGACY_INCOMPLETE_EVIDENCE`; missing facts are never invented.

## Consequences

Delivery, Evidence, and Board no longer observe half-written facts. Repository constructors no
longer create schemas opportunistically.

