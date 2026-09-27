"""Stable case directory contract for all product regression providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


class CaseContractError(ValueError):
    """A product supplied ambiguous or invalid regression metadata."""


@dataclass(frozen=True)
class CaseDefinition:
    suite: str
    suite_title: str
    suite_description: str
    target: str
    name: str
    title: str
    core_id: str
    summary: str
    enabled: bool
    tags: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CaseDefinition":
        """Normalize the two legacy catalog shapes at the platform boundary."""
        if not isinstance(value, dict):
            raise CaseContractError("用例定义必须是对象")
        suite, target = value.get("suite"), value.get("target")
        if not isinstance(suite, str) or not suite:
            raise CaseContractError("用例缺少套件 ID")
        if not isinstance(target, str) or not target.startswith(suite + "."):
            raise CaseContractError(f"用例目标不属于套件 {suite}: {target}")
        tags = value.get("tags") or []
        if not isinstance(tags, (list, tuple)) or any(not isinstance(tag, str) for tag in tags):
            raise CaseContractError(f"用例标签无效: {target}")
        title = value.get("title") or value.get("summary") or target
        if not isinstance(title, str) or not title:
            raise CaseContractError(f"用例标题无效: {target}")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CaseContractError(f"用例启用状态无效: {target}")
        return cls(
            suite=suite,
            suite_title=str(value.get("suite_title") or suite),
            suite_description=str(value.get("suite_description") or ""),
            target=target,
            name=str(value.get("name") or target.rsplit(".", 1)[-1]),
            title=title,
            core_id=str(value.get("core_id") or ""),
            summary=str(value.get("summary") or title),
            enabled=enabled,
            tags=tuple(tags),
        )

    def to_api(self) -> dict[str, Any]:
        result = asdict(self)
        result["tags"] = list(self.tags)
        return result


class CaseCatalog:
    """Validate a product's discovered cases before publishing them to users."""

    def __init__(self, cases: Iterable[dict[str, Any]]):
        definitions = [CaseDefinition.from_mapping(item) for item in cases]
        seen: set[str] = set()
        for case in definitions:
            if case.target in seen:
                raise CaseContractError(f"重复用例目标: {case.target}")
            seen.add(case.target)
        self.cases = tuple(definitions)

    def to_api(self) -> list[dict[str, Any]]:
        return [case.to_api() for case in self.cases]
