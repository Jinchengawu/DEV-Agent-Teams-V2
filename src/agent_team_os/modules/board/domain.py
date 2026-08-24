from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ...shared.events import ProductEvent

BoardColumn = Literal[
    "backlog",
    "plan-approval",
    "executing",
    "candidate-approval",
    "completed",
    "failed-cancelled",
]


class WorkItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str = "legacy-default"
    delivery_id: str
    title: str
    column: BoardColumn
    acceptance_ids: tuple[str, ...] = ()
    execution_identity: str | None = None
    available_commands: tuple[str, ...] = ()
    version: int


class BoardProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[WorkItem, ...]
    projection_sha256: str


class BoardProjector:
    """Rebuild the board only from committed Product Events."""

    _columns: dict[str, BoardColumn] = {
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
    _commands: dict[str, tuple[str, ...]] = {
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

    def rebuild(
        self, events: tuple[ProductEvent, ...], project_id: str | None = None
    ) -> BoardProjection:
        latest: dict[str, ProductEvent] = {}
        for event in sorted(
            (
                item
                for item in events
                if item.aggregate_type == "delivery"
                and (project_id is None or item.project_id == project_id)
            ),
            key=lambda item: (item.aggregate_id, item.aggregate_version),
        ):
            previous = latest.get(event.aggregate_id)
            if previous is None or event.aggregate_version >= previous.aggregate_version:
                latest[event.aggregate_id] = event

        items: list[WorkItem] = []
        for delivery_id, event in sorted(latest.items()):
            status = str(event.payload.get("status", "failed"))
            acceptance = event.payload.get("acceptance_ids", ())
            items.append(
                WorkItem(
                    id=delivery_id,
                    project_id=event.project_id
                    or str(event.payload.get("project_id") or "legacy-default"),
                    delivery_id=delivery_id,
                    title=str(event.payload.get("title") or delivery_id),
                    column=self._columns.get(status, "failed-cancelled"),
                    acceptance_ids=(
                        tuple(str(item) for item in acceptance)
                        if isinstance(acceptance, (list, tuple))
                        else ()
                    ),
                    execution_identity=(
                        str(event.payload["execution_identity"])
                        if event.payload.get("execution_identity")
                        else None
                    ),
                    available_commands=self._commands.get(status, ()),
                    version=event.aggregate_version,
                )
            )

        encoded = json.dumps(
            [item.model_dump(mode="json") for item in items],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return BoardProjection(
            items=tuple(items),
            projection_sha256=hashlib.sha256(encoded).hexdigest(),
        )
