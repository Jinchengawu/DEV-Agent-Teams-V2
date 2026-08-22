# ADR-0007: Web Console Uses Feature Slices

Status: accepted for V0.2.1.

## Decision

The console is organized into App, Entities, Features, and Shared Modules. A Feature may import
Entities and Shared Modules, never another Feature's implementation. Route composition belongs to
App. Generated transport contracts belong to Shared.

Every page implements loading, empty, error, conflict, and ready states. Every enabled control
invokes a real command. User-visible copy is Chinese except code, commands, protocol identifiers,
and immutable external identities shown with Chinese context.

The signature interaction is the evidence rail: when a Delivery is selected, its current Stage,
actor identity, decision status, and evidence integrity remain visible without repeating a generic
dashboard header on unrelated pages.

