"""Project typed step journals into operation and scene events."""

import json
from pathlib import Path

from .event_contracts import EntityDiscovered
from .filestore import FileStore
from .scene import emit_observation


class ArtifactProgressObserver:
    """把当前运行写出的步骤日志转换为平台事件。"""

    def __init__(self, root, target, started_at, *, parse_observations):
        self.root = Path(root)
        self.parse_observations = parse_observations
        self.target = target
        self.started_at = started_at - 1
        self.seen: dict[tuple[str, int], str] = {}
        self.observed_steps: set[tuple[str, int]] = set()
        self.discovered_entities: set[str] = set()

    def poll(self, store: FileStore, task_id: str) -> None:
        if not self.root.is_dir():
            return
        for path in self.root.glob("*/cases/*/steps.json"):
            case = path.parent.name
            if self.target not in {"all", "failed", case, case.split(".", 1)[0]}:
                continue
            try:
                if path.stat().st_mtime < self.started_at:
                    continue
                steps = json.loads(path.read_text(encoding="utf-8")).get("steps", [])
            except (OSError, ValueError, AttributeError):
                continue
            for index, step in enumerate(steps):
                key = (case, index)
                result = str(step.get("result") or step.get("status") or "RUNNING")
                previous = self.seen.get(key)
                if previous is None:
                    store.tasks.add_event(
                        task_id,
                        "step.started",
                        {
                            "title": step.get("title") or f"步骤 {index + 1}",
                            "target": case,
                            "step_index": index,
                            "expected": step.get("expected"),
                            "artifact": str(path),
                        },
                    )
                if result in {"PASS", "FAIL"} and previous != result:
                    store.tasks.add_event(
                        task_id,
                        "assertion.checked",
                        {
                            "title": step.get("title") or f"步骤 {index + 1}",
                            "target": case,
                            "step_index": index,
                            "expected": step.get("expected"),
                            "actual": step.get("actual"),
                            "result": result,
                            "artifact": str(path),
                        },
                    )
                if result == "PASS" and key not in self.observed_steps:
                    for entry in step.get("execution") or []:
                        output = entry.get("text", "")
                        facts = self.parse_observations(output)
                        for fact in facts:
                            entity_id = fact.details["entity_id"]
                            if entity_id not in self.discovered_entities:
                                store.tasks.add_event(
                                    task_id,
                                    "scene.entity.discovered",
                                    EntityDiscovered(
                                        id=entity_id,
                                        label=fact.details.get("label", entity_id),
                                        kind=fact.kind,
                                        group=fact.details.get("group_name"),
                                        details={"artifact": str(path)},
                                    ).model_dump(),
                                )
                                self.discovered_entities.add(entity_id)
                            emit_observation(
                                store,
                                task_id,
                                entity_id,
                                fact.state,
                                fact.kind,
                                {**fact.details, "artifact": str(path)},
                            )
                    self.observed_steps.add(key)
                self.seen[key] = result
