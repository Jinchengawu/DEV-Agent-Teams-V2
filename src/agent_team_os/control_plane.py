"""Durable product control plane layered above ACWM runtime contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

import httpx
from acwm.config import load_journeys
from acwm.domain import JourneyDefinition, StageDefinition
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .delivery import DeliveryRun
from .journey import resolve_journey_fingerprint
from .modules.agents import RuntimeAdapterCatalog, RuntimeAdapterDescriptor


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HealthResult(ImmutableModel):
    status: Literal["unknown", "ready", "failed"]
    identity: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentInstance(ImmutableModel):
    id: str
    name: str
    runtime_type: Literal["hermes-acp", "hermes-http", "codex-cli"]
    connection: dict[str, str]
    credential_ref: str | None = None
    features: tuple[str, ...] = ()
    adapter_id: str | None = None
    adapter_version: str | None = None
    features_source: Literal["installed-acwm-adapter-manifest"] | None = None
    enabled: bool = True
    version: int = 1
    health: HealthResult = Field(default_factory=lambda: HealthResult(status="unknown"))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("connection")
    @classmethod
    def connection_contains_no_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = {"secret", "token", "password", "api_key", "authorization"}
        if any(key.lower().replace("-", "_") in forbidden for key in value):
            raise ValueError("connection may contain references but not secret values")
        return value

    @field_validator("credential_ref")
    @classmethod
    def credential_is_a_reference(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"(?:env:[A-Z][A-Z0-9_]*|keychain:[A-Za-z0-9._-]+)", value
        ):
            raise ValueError("credential_ref must be an env or keychain reference")
        return value


class AgentInstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    runtime_type: Literal["hermes-acp", "hermes-http", "codex-cli"]
    connection: dict[str, str]
    credential_ref: str | None = None

    @field_validator("connection")
    @classmethod
    def connection_contains_no_secrets(cls, value: dict[str, str]) -> dict[str, str]:
        return AgentInstance.connection_contains_no_secrets(value)

    @field_validator("credential_ref")
    @classmethod
    def credential_is_a_reference(cls, value: str | None) -> str | None:
        return AgentInstance.credential_is_a_reference(value)


class AgentInstancePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    connection: dict[str, str] | None = None
    credential_ref: str | None = None
    enabled: bool | None = None

    @field_validator("connection")
    @classmethod
    def connection_contains_no_secrets(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else AgentInstance.connection_contains_no_secrets(value)

    @field_validator("credential_ref")
    @classmethod
    def credential_is_a_reference(cls, value: str | None) -> str | None:
        return AgentInstance.credential_is_a_reference(value)


class CapabilityBinding(ImmutableModel):
    capability_id: str
    instance_id: str
    instance_version: int
    version: int
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    expected_version: int = Field(ge=0)


class JourneyDraft(ImmutableModel):
    id: str
    name: str
    definition: dict[str, object]
    layout: dict[str, object] = Field(default_factory=dict)
    version: int = 1
    validation_status: Literal["unknown", "valid", "invalid"] = "unknown"
    validation_errors: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JourneyDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    definition: dict[str, object]
    layout: dict[str, object] = Field(default_factory=dict)


class JourneyDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = None
    definition: dict[str, object] | None = None
    layout: dict[str, object] | None = None


class JourneyRevision(ImmutableModel):
    journey_id: str
    revision: int
    definition: dict[str, object]
    binding_snapshot: dict[str, dict[str, object]]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkItem(ImmutableModel):
    id: str
    delivery_id: str
    title: str
    column: Literal[
        "backlog",
        "plan-approval",
        "executing",
        "candidate-approval",
        "completed",
        "failed-cancelled",
    ]
    acceptance_ids: tuple[str, ...] = ()
    execution_identity: str | None = None
    available_commands: tuple[str, ...] = ()
    version: int


class WorkItemCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal[
        "approve-plan",
        "reject-plan",
        "accept-candidate",
        "reject-candidate",
        "cancel",
    ]
    expected_version: int = Field(ge=1)


class SourceLink(ImmutableModel):
    source_kind: str
    source_id: str
    delivery_id: str | None = None


class KnowledgeDocument(ImmutableModel):
    id: str
    title: str
    media_type: Literal["text/plain", "text/markdown", "application/json"]
    artifact_type: str
    content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = 1
    sources: tuple[SourceLink, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    media_type: Literal["text/plain", "text/markdown", "application/json"]
    content: str = Field(min_length=1, max_length=1_000_000)


class ControlEvent(ImmutableModel):
    sequence: int
    event_type: str
    aggregate_id: str
    payload: dict[str, object]
    created_at: datetime


class InstanceHealthProbe(Protocol):
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult: ...


class SystemHealthProbe:
    async def check(self, runtime_type: str, connection: dict[str, str]) -> HealthResult:
        started = time.monotonic()
        if runtime_type == "hermes-http":
            endpoint = connection.get("endpoint", "")
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{endpoint.rstrip('/')}/health")
                    response.raise_for_status()
                status: Literal["ready", "failed"] = "ready"
                identity = "hermes-http"
            except (httpx.HTTPError, ValueError):
                status = "failed"
                identity = None
        else:
            default_command = "codex" if runtime_type == "codex-cli" else "hermes"
            command = connection.get("command", default_command)
            from shutil import which

            executable = which(command)
            if executable is None:
                status = "failed"
            else:
                arguments = (
                    [executable, "login", "status"]
                    if runtime_type == "codex-cli"
                    else [executable, "--version"]
                )
                try:
                    process = await asyncio.create_subprocess_exec(
                        *arguments,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(process.wait(), timeout=10)
                    status = "ready" if process.returncode == 0 else "failed"
                except TimeoutError:
                    status = "failed"
            identity = runtime_type if status == "ready" else None
        return HealthResult(
            status=status,
            identity=identity,
            latency_ms=max(1, int((time.monotonic() - started) * 1000)),
            error_code=None if status == "ready" else "INSTANCE_UNAVAILABLE",
        )


class ControlPlaneService:
    def __init__(
        self,
        database: Path,
        *,
        probe: InstanceHealthProbe | None = None,
        config_root: Path | None = None,
        adapter_catalog: RuntimeAdapterCatalog | None = None,
    ) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._probe = probe or SystemHealthProbe()
        self._config_root = config_root
        self._adapter_catalog = adapter_catalog or RuntimeAdapterCatalog()
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS control_records(
                kind TEXT NOT NULL, id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                PRIMARY KEY(kind, id))"""
            )
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                document_id UNINDEXED, title, content, artifact_type, source_id)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS control_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL)"""
            )

    def create_instance(self, request: AgentInstanceCreate) -> AgentInstance:
        adapter = self._adapter_catalog.for_runtime(request.runtime_type)
        instance = AgentInstance(
            id=str(uuid4()),
            **request.model_dump(),
            features=adapter.features,
            adapter_id=adapter.id,
            adapter_version=adapter.version,
            features_source=adapter.features_source,
        )
        self._save("agent-instance", instance.id, instance.model_dump_json())
        return instance

    def list_instances(self) -> tuple[AgentInstance, ...]:
        return tuple(
            self._with_trusted_adapter(AgentInstance.model_validate_json(value))
            for value in self._list("agent-instance")
        )

    def get_instance(self, instance_id: str) -> AgentInstance:
        value = self._get("agent-instance", instance_id)
        if value is None:
            raise KeyError(instance_id)
        return self._with_trusted_adapter(AgentInstance.model_validate_json(value))

    def list_runtime_adapters(self) -> tuple[RuntimeAdapterDescriptor, ...]:
        return self._adapter_catalog.list()

    def _with_trusted_adapter(self, instance: AgentInstance) -> AgentInstance:
        adapter = self._adapter_catalog.for_runtime(instance.runtime_type)
        return instance.model_copy(
            update={
                "features": adapter.features,
                "adapter_id": adapter.id,
                "adapter_version": adapter.version,
                "features_source": adapter.features_source,
            }
        )

    def patch_instance(self, instance_id: str, request: AgentInstancePatch) -> AgentInstance:
        current = self.get_instance(instance_id)
        if current.version != request.expected_version:
            raise RuntimeError("agent instance version conflict")
        values = request.model_dump(exclude_none=True, exclude={"expected_version"})
        updated = current.model_copy(
            update={
                **values,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._save("agent-instance", updated.id, updated.model_dump_json())
        return updated

    async def check_instance(self, instance_id: str) -> AgentInstance:
        current = self.get_instance(instance_id)
        missing_credential = (
            current.credential_ref is not None
            and current.credential_ref.startswith("env:")
            and not os.environ.get(current.credential_ref.removeprefix("env:"))
        )
        health = (
            HealthResult(status="failed", error_code="CREDENTIAL_REFERENCE_MISSING")
            if missing_credential
            else await self._probe.check(current.runtime_type, current.connection)
        )
        updated = current.model_copy(
            update={
                "health": health,
                "updated_at": datetime.now(UTC),
            }
        )
        self._save("agent-instance", updated.id, updated.model_dump_json())
        return updated

    def put_binding(self, capability_id: str, request: BindingRequest) -> CapabilityBinding:
        instance = self.get_instance(request.instance_id)
        if not instance.enabled or instance.health.status != "ready":
            raise ValueError("instance must be enabled and healthy")
        if capability_id == "codex-backend" and instance.runtime_type != "codex-cli":
            raise ValueError("codex-backend requires a codex-cli instance")
        if capability_id == "codex-backend" and "workspace.cwd_binding" not in instance.features:
            raise ValueError("codex-backend requires the cwd-binding feature")
        if capability_id.startswith("hermes-") and not (
            {"io.text.final"} & set(instance.features)
        ):
            raise ValueError(f"{capability_id} requires structured text output")
        current_json = self._get("capability-binding", capability_id)
        current = (
            None if current_json is None else CapabilityBinding.model_validate_json(current_json)
        )
        current_version = 0 if current is None else current.version
        if current_version != request.expected_version:
            raise RuntimeError("binding version conflict")
        binding = CapabilityBinding(
            capability_id=capability_id,
            instance_id=instance.id,
            instance_version=instance.version,
            version=current_version + 1,
        )
        self._save("capability-binding", capability_id, binding.model_dump_json())
        return binding

    def get_binding(self, capability_id: str) -> CapabilityBinding:
        value = self._get("capability-binding", capability_id)
        if value is None:
            raise KeyError(capability_id)
        return CapabilityBinding.model_validate_json(value)

    def list_bindings(self) -> tuple[CapabilityBinding, ...]:
        return tuple(
            sorted(
                (
                    CapabilityBinding.model_validate_json(value)
                    for value in self._list("capability-binding")
                ),
                key=lambda binding: binding.capability_id,
            )
        )

    def create_draft(self, request: JourneyDraftCreate) -> JourneyDraft:
        draft = JourneyDraft(id=str(uuid4()), **request.model_dump())
        self._save("journey-draft", draft.id, draft.model_dump_json())
        return draft

    def list_drafts(self) -> tuple[JourneyDraft, ...]:
        return tuple(
            JourneyDraft.model_validate_json(value) for value in self._list("journey-draft")
        )

    def get_draft(self, draft_id: str) -> JourneyDraft:
        value = self._get("journey-draft", draft_id)
        if value is None:
            raise KeyError(draft_id)
        return JourneyDraft.model_validate_json(value)

    def patch_draft(self, draft_id: str, request: JourneyDraftPatch) -> JourneyDraft:
        draft = self.get_draft(draft_id)
        if draft.version != request.expected_version:
            raise RuntimeError("draft version conflict")
        values = request.model_dump(exclude_none=True, exclude={"expected_version"})
        updated = draft.model_copy(
            update={
                **values,
                "version": draft.version + 1,
                "validation_status": "unknown",
                "validation_errors": (),
                "updated_at": datetime.now(UTC),
            }
        )
        self._save("journey-draft", draft.id, updated.model_dump_json())
        return updated

    def validate_draft(self, draft_id: str) -> JourneyDraft:
        draft = self.get_draft(draft_id)
        errors: list[str] = []
        try:
            definition = JourneyDefinition.model_validate(draft.definition)
            if self._config_root is None:
                raise ValueError("ACWM config root is not configured")
            resolve_journey_fingerprint(self._config_root, definition)
            for step in definition.steps:
                if not isinstance(step, StageDefinition):
                    continue
                for capability_id in step.bindings.values():
                    binding = self.get_binding(capability_id)
                    instance = self.get_instance(binding.instance_id)
                    if not instance.enabled or instance.health.status != "ready":
                        errors.append(f"{capability_id} is not bound to a healthy instance")
                    if binding.instance_version != instance.version:
                        errors.append(f"{capability_id} binding uses a stale instance version")
        except (ValueError, KeyError) as error:
            errors.append(str(error))
        updated = draft.model_copy(
            update={
                "validation_status": "invalid" if errors else "valid",
                "validation_errors": tuple(errors),
                "updated_at": datetime.now(UTC),
            }
        )
        self._save("journey-draft", draft.id, updated.model_dump_json())
        return updated

    def publish_draft(self, draft_id: str) -> JourneyRevision:
        draft = self.validate_draft(draft_id)
        if draft.validation_status != "valid" or self._config_root is None:
            raise ValueError("journey draft is invalid")
        definition = JourneyDefinition.model_validate(draft.definition)
        capability_ids = {
            capability_id
            for step in definition.steps
            if isinstance(step, StageDefinition)
            for capability_id in step.bindings.values()
        }
        snapshot: dict[str, dict[str, object]] = {}
        for capability_id in sorted(capability_ids):
            binding = self.get_binding(capability_id)
            instance = self.get_instance(binding.instance_id)
            snapshot[capability_id] = {
                "instance_id": instance.id,
                "instance_version": instance.version,
                "runtime_type": instance.runtime_type,
                "identity": instance.health.identity,
            }
        journey_id = definition.id
        existing = self._list(f"journey-revision:{journey_id}")
        revision = JourneyRevision(
            journey_id=journey_id,
            revision=len(existing) + 1,
            definition=draft.definition,
            binding_snapshot=snapshot,
            fingerprint=resolve_journey_fingerprint(self._config_root, definition),
        )
        self._save(
            f"journey-revision:{journey_id}",
            str(revision.revision),
            revision.model_dump_json(),
        )
        return revision

    def get_revision(self, journey_id: str, revision: int) -> JourneyRevision:
        value = self._get(f"journey-revision:{journey_id}", str(revision))
        if value is None:
            raise KeyError(journey_id)
        return JourneyRevision.model_validate_json(value)

    def list_journeys(self) -> tuple[JourneyRevision, ...]:
        with sqlite3.connect(self.database) as connection:
            kinds = connection.execute(
                "SELECT DISTINCT kind FROM control_records WHERE kind LIKE 'journey-revision:%'"
            ).fetchall()
        latest: list[JourneyRevision] = []
        for (kind,) in kinds:
            values = self._list(str(kind))
            if values:
                latest.append(JourneyRevision.model_validate_json(values[-1]))
        return tuple(sorted(latest, key=lambda item: item.journey_id))

    def resolve_revision(self, revision_id: str | None = None) -> JourneyRevision:
        if revision_id is None:
            journeys = self.list_journeys()
            backend = [item for item in journeys if item.journey_id == "backend-delivery"]
            if not backend:
                raise KeyError("backend-delivery")
            return backend[-1]
        journey_id, separator, revision_text = revision_id.rpartition(":")
        if not separator or not revision_text.isdigit():
            raise KeyError(revision_id)
        return self.get_revision(journey_id, int(revision_text))

    def ensure_revision_available(self, revision: JourneyRevision) -> None:
        for capability_id, binding in revision.binding_snapshot.items():
            instance_id = str(binding["instance_id"])
            try:
                instance = self.get_instance(instance_id)
            except KeyError as error:
                raise ValueError(f"{capability_id} instance is no longer registered") from error
            if not instance.enabled or instance.health.status != "ready":
                raise ValueError(f"{capability_id} instance is disabled or unhealthy")
            published_version = binding.get("instance_version")
            if not isinstance(published_version, int):
                raise ValueError(f"{capability_id} binding snapshot is invalid")
            if instance.version != published_version:
                raise ValueError(f"{capability_id} instance changed after publication")

    def import_builtin_journey(
        self, *, planning_identity: str, execution_identity: str
    ) -> JourneyRevision:
        """Import the checked-in ACWM Journey once as immutable revision 1."""
        try:
            return self.get_revision("backend-delivery", 1)
        except KeyError:
            pass
        if self._config_root is None:
            raise ValueError("ACWM config root is not configured")
        definition = load_journeys(self._config_root / "journeys.yaml")["backend-delivery"]
        identities = {
            "hermes-pm": planning_identity,
            "hermes-project-admin": planning_identity,
            "codex-backend": execution_identity,
        }
        snapshot = {
            capability_id: {
                "instance_id": f"builtin:{identity}",
                "instance_version": 1,
                "runtime_type": "codex-cli",
                "identity": identity,
            }
            for capability_id, identity in identities.items()
        }
        for identity in sorted(set(identities.values())):
            instance = AgentInstance(
                id=f"builtin:{identity}",
                name=(
                    "Codex simulated Hermes planner"
                    if identity == planning_identity
                    else "Codex backend executor"
                ),
                runtime_type="codex-cli",
                connection={"command": "codex"},
                health=HealthResult(status="ready", identity=identity, latency_ms=0),
            )
            self._save("agent-instance", instance.id, instance.model_dump_json())
        for capability_id, identity in identities.items():
            binding = CapabilityBinding(
                capability_id=capability_id,
                instance_id=f"builtin:{identity}",
                instance_version=1,
                version=1,
            )
            self._save("capability-binding", capability_id, binding.model_dump_json())
        revision = JourneyRevision(
            journey_id=definition.id,
            revision=1,
            definition=definition.model_dump(mode="json"),
            binding_snapshot=snapshot,
            fingerprint=resolve_journey_fingerprint(self._config_root, definition),
        )
        self._save("journey-revision:backend-delivery", "1", revision.model_dump_json())
        self.create_document(
            KnowledgeDocumentCreate(
                title="Backend Delivery Journey · Revision 1",
                media_type="application/json",
                content=revision.model_dump_json(indent=2),
            ),
            artifact_type="journey-revision",
            sources=(
                SourceLink(
                    source_kind="journey-revision",
                    source_id="backend-delivery:1",
                ),
            ),
        )
        return revision

    def board(self, deliveries: tuple[DeliveryRun, ...]) -> tuple[WorkItem, ...]:
        columns = {
            "queued": "backlog",
            "planning": "backlog",
            "awaiting_plan_decision": "plan-approval",
            "executing": "executing",
            "verifying": "executing",
            "awaiting_candidate_decision": "candidate-approval",
            "applying": "executing",
            "completed": "completed",
            "failed": "failed-cancelled",
            "cancelled": "failed-cancelled",
            "rejected": "failed-cancelled",
        }
        commands = {
            "awaiting_plan_decision": ("approve-plan", "reject-plan", "cancel"),
            "awaiting_candidate_decision": (
                "accept-candidate",
                "reject-candidate",
                "cancel",
            ),
            "queued": ("cancel",),
            "planning": ("cancel",),
            "executing": ("cancel",),
            "verifying": ("cancel",),
        }
        items = tuple(
            WorkItem(
                id=delivery.id,
                delivery_id=delivery.id,
                title=delivery.task.title if delivery.task else delivery.user_request,
                column=columns[delivery.status],  # type: ignore[arg-type]
                acceptance_ids=(delivery.task.acceptance_ids if delivery.task else ()),
                execution_identity=delivery.execution_identity,
                available_commands=commands.get(delivery.status, ()),
                version=delivery.version,
            )
            for delivery in deliveries
        )
        for item in items:
            self._save("work-item", item.id, item.model_dump_json())
        return items

    def create_document(
        self,
        request: KnowledgeDocumentCreate,
        *,
        artifact_type: str = "manual",
        sources: tuple[SourceLink, ...] = (),
    ) -> KnowledgeDocument:
        digest = hashlib.sha256(request.content.encode()).hexdigest()
        for value in self._list("knowledge-document"):
            existing = KnowledgeDocument.model_validate_json(value)
            if existing.sha256 == digest:
                merged_sources = tuple(dict.fromkeys((*existing.sources, *sources)))
                if merged_sources != existing.sources:
                    existing = existing.model_copy(update={"sources": merged_sources})
                    self._save("knowledge-document", existing.id, existing.model_dump_json())
                    with sqlite3.connect(self.database) as connection:
                        connection.execute(
                            "UPDATE knowledge_fts SET source_id=? WHERE document_id=?",
                            (
                                " ".join(source.source_id for source in merged_sources),
                                existing.id,
                            ),
                        )
                return existing
        document = KnowledgeDocument(
            id=str(uuid4()),
            title=request.title,
            media_type=request.media_type,
            artifact_type=artifact_type,
            content=request.content,
            sha256=digest,
            sources=sources,
        )
        self._save("knowledge-document", document.id, document.model_dump_json())
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO knowledge_fts VALUES(?,?,?,?,?)",
                (
                    document.id,
                    document.title,
                    document.content,
                    document.artifact_type,
                    " ".join(source.source_id for source in sources),
                ),
            )
        return document

    def sync_delivery_documents(self, delivery: DeliveryRun) -> None:
        artifacts = (
            ("requirement", delivery.requirements),
            ("task", delivery.task),
            ("candidate", delivery.candidate),
            ("verification", delivery.verification),
            ("plan-gate", delivery.plan_gate),
            ("candidate-gate", delivery.candidate_gate),
            ("apply-receipt", delivery.apply_receipt),
        )
        for artifact_type, artifact in artifacts:
            if artifact is None:
                continue
            content = artifact.model_dump_json(indent=2)
            self.create_document(
                KnowledgeDocumentCreate(
                    title=f"{delivery.user_request} · {artifact_type}",
                    media_type="application/json",
                    content=content,
                ),
                artifact_type=artifact_type,
                sources=(
                    SourceLink(
                        source_kind=artifact_type,
                        source_id=f"{delivery.id}:{artifact_type}",
                        delivery_id=delivery.id,
                    ),
                ),
            )

    def search_documents(self, query: str) -> tuple[KnowledgeDocument, ...]:
        try:
            with sqlite3.connect(self.database) as connection:
                rows = connection.execute(
                    "SELECT document_id FROM knowledge_fts WHERE knowledge_fts MATCH ?",
                    (query,),
                ).fetchall()
        except sqlite3.OperationalError:
            safe_query = '"' + query.replace('"', '""') + '"'
            with sqlite3.connect(self.database) as connection:
                rows = connection.execute(
                    "SELECT document_id FROM knowledge_fts WHERE knowledge_fts MATCH ?",
                    (safe_query,),
                ).fetchall()
        documents: list[KnowledgeDocument] = []
        for row in rows:
            value = self._get("knowledge-document", str(row[0]))
            if value is not None:
                documents.append(KnowledgeDocument.model_validate_json(value))
        return tuple(documents)

    def get_document(self, document_id: str) -> KnowledgeDocument:
        value = self._get("knowledge-document", document_id)
        if value is None:
            raise KeyError(document_id)
        return KnowledgeDocument.model_validate_json(value)

    def list_documents(self) -> tuple[KnowledgeDocument, ...]:
        return tuple(
            KnowledgeDocument.model_validate_json(value)
            for value in self._list("knowledge-document")
        )

    def list_events(self, after: int = 0) -> tuple[ControlEvent, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT sequence,event_type,aggregate_id,payload_json,created_at
                FROM control_events WHERE sequence>? ORDER BY sequence LIMIT 200""",
                (after,),
            ).fetchall()
        return tuple(
            ControlEvent(
                sequence=int(row[0]),
                event_type=str(row[1]),
                aggregate_id=str(row[2]),
                payload=json.loads(str(row[3])),
                created_at=datetime.fromisoformat(str(row[4])),
            )
            for row in rows
        )

    def _save(self, kind: str, record_id: str, value: str) -> None:
        with sqlite3.connect(self.database) as connection:
            prior = connection.execute(
                "SELECT snapshot_json FROM control_records WHERE kind=? AND id=?",
                (kind, record_id),
            ).fetchone()
            connection.execute(
                """INSERT INTO control_records(kind,id,snapshot_json) VALUES(?,?,?)
                ON CONFLICT(kind,id) DO UPDATE SET snapshot_json=excluded.snapshot_json""",
                (kind, record_id, value),
            )
            if prior is None or str(prior[0]) != value:
                connection.execute(
                    """INSERT INTO control_events(
                    event_type,aggregate_id,payload_json,created_at) VALUES(?,?,?,?)""",
                    (f"{kind}.updated", record_id, value, datetime.now(UTC).isoformat()),
                )

    def _get(self, kind: str, record_id: str) -> str | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM control_records WHERE kind=? AND id=?",
                (kind, record_id),
            ).fetchone()
        return None if row is None else str(row[0])

    def _list(self, kind: str) -> tuple[str, ...]:
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM control_records WHERE kind=? ORDER BY rowid",
                (kind,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)
