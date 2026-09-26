"""Versioned contracts for events consumed by reports and scene playback."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EntityObserved(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    entity_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    source: str = Field(min_length=1)
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))
    details: dict[str, Any] = Field(default_factory=dict)


class EntityDiscovered(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    group: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SceneObservationError(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)


class SceneAction(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    entity_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str = Field(min_length=1)
    success: bool | None = None


class SceneTopology(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    product_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
