import pytest
from platform_app.event_contracts import (
    EntityDiscovered,
    EntityObserved,
    SceneAction,
    SceneObservationError,
    SceneTopology,
)
from pydantic import ValidationError


def test_entity_observation_requires_identity_and_source():
    with pytest.raises(ValidationError):
        EntityObserved(entity_id="", state="ready", source="")
    event = EntityObserved(entity_id="node1", state="ready", source="pgcluster.status")
    assert event.model_dump()["schema_version"] == "1.0"
    assert event.observed_at


def test_observation_error_requires_message():
    with pytest.raises(ValidationError):
        SceneObservationError(source="proxy", message="")


def test_discovered_entity_requires_renderable_identity():
    with pytest.raises(ValidationError):
        EntityDiscovered(id="", label="node", kind="database")
    event = EntityDiscovered(id="node:1", label="node", kind="database")
    assert event.model_dump()["schema_version"] == "1.0"


def test_scene_action_and_topology_have_versioned_shape():
    action = SceneAction(entity_id="endpoint:lab", action="database.check", target="lab")
    topology = SceneTopology(product_id="fbasecman", environment_id="lab")
    assert action.model_dump()["schema_version"] == "1.0"
    assert topology.model_dump()["entities"] == []
