"""Product-neutral facts for test and operation topology playback.

Configured relationships and observed runtime state are separate events. A
configured primary is never presented as an observed, healthy primary.
"""

from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .event_contracts import (
    EntityObserved,
    SceneAction,
    SceneObservationError,
    SceneTopology,
)
from .filestore import FileStore
from .topology import configured_topology, observed_status


class SceneEntity(BaseModel):
    id: str
    label: str
    kind: str
    group: str | None = None
    configured_role: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SceneRelation(BaseModel):
    id: str
    source: str
    target: str
    kind: str


def endpoint_id(environment: dict) -> str:
    return "endpoint:" + environment["id"]


def emit_configured_scene(store: FileStore, settings: Settings, task_id: str,
                          environment: dict) -> None:
    """Publish a scene baseline without claiming that any node is healthy."""
    entities = [SceneEntity(
        id=endpoint_id(environment), label=environment["title"],
        kind="endpoint", details={
            "host": environment["host"], "port": environment["port"],
        },
    )]
    relations: list[SceneRelation] = []
    config = environment.get("deployment_config")
    if config and Path(config).is_file() and environment.get("deployment_target"):
        try:
            topology = configured_topology(settings, environment)
        except (OSError, ValueError, RuntimeError, TimeoutExpired):
            topology = None
        if topology:
            entities.extend(SceneEntity(
                id=node["id"], label=node.get("label", node["id"]),
                kind="database", group=node.get("group"),
                configured_role=node.get("role"),
                details={"host": node.get("host"), "port": node.get("port")},
            ) for node in topology["nodes"])
            relations.extend(SceneRelation(**edge) for edge in topology["edges"])
    store.add_event(task_id, "scene.topology.configured", SceneTopology(
        product_id=environment["product_id"], environment_id=environment["id"],
        entities=[entity.model_dump() for entity in entities],
        relations=[relation.model_dump() for relation in relations],
    ).model_dump())


def emit_observation(store: FileStore, task_id: str, entity_id: str, state: str,
                     source: str, details: dict[str, Any] | None = None) -> None:
    event = EntityObserved(
        entity_id=entity_id, state=state, source=source, details=details or {},
    )
    store.add_event(task_id, "scene.entity.observed", event.model_dump())


def emit_action(store: FileStore, task_id: str, environment: dict, action: str,
                target: str, phase: str, success: bool | None = None) -> None:
    entity_id = target if action.startswith("deployment.") and "." not in target else endpoint_id(environment)
    store.add_event(task_id, f"scene.action.{phase}", SceneAction(
        entity_id=entity_id, action=action, target=target, success=success,
    ).model_dump())


def emit_pgcluster_status(store: FileStore, settings: Settings, task_id: str,
                          environment: dict) -> None:
    """Add measured instance states after a deployment operation."""
    if not environment.get("deployment_config"):
        return
    try:
        statuses = observed_status(settings, environment)
    except (OSError, ValueError, RuntimeError, TimeoutExpired) as exc:
        store.add_event(task_id, "scene.observation.error", SceneObservationError(
            source="pgcluster.status", message=str(exc),
        ).model_dump())
        return
    for name, details in statuses.items():
        running = details.get("running")
        state = "running" if running is True else "stopped" if running is False else "unknown"
        emit_observation(store, task_id, name, state, "pgcluster.status", details)
