"""Modern product package discovery and manifest validation."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
from typing import Any

import yaml

PRODUCT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
PLUGIN_API = "v1"


class ProductManifestError(ValueError):
    """Raised when a product package cannot be loaded safely."""


@dataclass(frozen=True)
class LicenseDescriptor:
    product_code: str
    allowed_versions: tuple[str, ...]


@dataclass(frozen=True)
class TestProfile:
    id: str
    title: str
    suites: tuple[str, ...]
    deployment_targets: tuple[str, ...]

    def accepts(self, deployment_target: str | None, target: str,
                requested_profile: str | None = None) -> bool:
        suite = requested_profile if target == "all" else target.split(".", 1)[0]
        return (bool(deployment_target)
                and (suite in self.suites or "*" in self.suites)
                and (requested_profile is None or requested_profile == self.id)
                and any(fnmatchcase(deployment_target, pattern)
                        for pattern in self.deployment_targets))


@dataclass(frozen=True)
class ProductAction:
    id: str
    title: str
    capability: str
    changes_environment: bool = False
    parameter_schema: dict[str, Any] | None = None
    validate_target: bool = False


@dataclass(frozen=True)
class ProductManifest:
    id: str
    title: str
    plugin_api: str
    regression_sdk: str | None
    package_root: Path
    versions: tuple[str, ...]
    capabilities: dict[str, str]
    actions: tuple[ProductAction, ...]
    cli: dict[str, Path]
    license: LicenseDescriptor | None
    source_root: Path | None
    test_profiles: tuple[TestProfile, ...]
    provider_path: Path | None

    @property
    def source_path(self) -> str:
        return str(self.package_root)

    def cli_path(self, name: str) -> Path:
        """Resolve a declared CLI entry without allowing path escape."""
        try:
            relative = self.cli[name]
        except KeyError as exc:
            raise ProductManifestError(f"CLI entry is not declared: {name}") from exc
        return (self.package_root / relative).resolve()


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductManifestError(f"{field} must be a non-empty string")
    return value.strip()


def _tuple_strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProductManifestError(f"{field} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def validate_parameters(action: ProductAction, parameters: dict[str, Any]) -> None:
    """Validate the small JSON-schema subset used by product actions."""
    schema = action.parameter_schema
    if not schema:
        return
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for name in required:
        if name not in parameters:
            raise ProductManifestError(f"动作 {action.id} 缺少参数: {name}")
    for name, rule in properties.items():
        if name not in parameters or not isinstance(rule, dict):
            continue
        expected = rule.get("type")
        value = parameters[name]
        valid = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }.get(expected, True)
        if not valid:
            raise ProductManifestError(f"动作 {action.id} 参数 {name} 类型错误，应为 {expected}")
        choices = rule.get("enum")
        if choices is not None and value not in choices:
            raise ProductManifestError(f"动作 {action.id} 参数 {name} 值无效")


def load_manifest(package_root: Path) -> ProductManifest:
    package_root = package_root.resolve()
    manifest_path = package_root / "product.yaml"
    if not package_root.is_dir():
        raise ProductManifestError(f"product package does not exist: {package_root}")
    if not manifest_path.is_file():
        raise ProductManifestError(f"missing product.yaml: {package_root}")
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProductManifestError(f"cannot read {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProductManifestError("product.yaml must contain a mapping")

    product_id = _string(raw.get("id"), "id")
    if not PRODUCT_ID.fullmatch(product_id):
        raise ProductManifestError("id must match ^[a-z][a-z0-9_-]{1,63}$")
    if product_id != package_root.name:
        raise ProductManifestError(f"product id {product_id} must match directory {package_root.name}")
    plugin_api = _string(raw.get("plugin_api"), "plugin_api")
    if plugin_api != PLUGIN_API:
        raise ProductManifestError(f"unsupported plugin_api: {plugin_api}")
    title = _string(raw.get("title"), "title")
    versions_raw = raw.get("versions", [])
    if not isinstance(versions_raw, list):
        raise ProductManifestError("versions must be a list")
    versions: list[str] = []
    for item in versions_raw:
        if not isinstance(item, dict):
            raise ProductManifestError("each version must be a mapping")
        versions.append(_string(item.get("id"), "versions[].id"))
    if len(set(versions)) != len(versions):
        raise ProductManifestError("versions must be unique")

    capabilities_raw = raw.get("capabilities", {})
    if not isinstance(capabilities_raw, dict):
        raise ProductManifestError("capabilities must be a mapping")
    capabilities = {
        _string(key, "capability name"): _string(value, f"capabilities.{key}")
        for key, value in capabilities_raw.items()
    }

    if "tests" in capabilities:
        from platform_regress.sdk import SDK_VERSION
        if raw.get("regression_sdk") != SDK_VERSION:
            raise ProductManifestError(f"unsupported regression_sdk: {raw.get('regression_sdk')}")

    actions_raw = raw.get("actions", [])
    if not isinstance(actions_raw, list):
        raise ProductManifestError("actions must be a list")
    actions: list[ProductAction] = []
    for item in actions_raw:
        if not isinstance(item, dict):
            raise ProductManifestError("each action must be a mapping")
        action_id = _string(item.get("id"), "actions[].id")
        action_capability = _string(item.get("capability"), f"actions.{action_id}.capability")
        changes = item.get("changes_environment", False)
        if not isinstance(changes, bool):
            raise ProductManifestError(f"actions.{action_id}.changes_environment must be boolean")
        schema = item.get("parameters")
        if schema is not None and not isinstance(schema, dict):
            raise ProductManifestError(f"actions.{action_id}.parameters must be a mapping")
        check_target = item.get("validate_target", False)
        if not isinstance(check_target, bool):
            raise ProductManifestError(f"actions.{action_id}.validate_target must be boolean")
        actions.append(ProductAction(
            action_id, _string(item.get("title"), f"actions.{action_id}.title"),
            action_capability, changes, schema, check_target,
        ))
    if len({item.id for item in actions}) != len(actions):
        raise ProductManifestError("actions must be unique")

    profiles_raw = raw.get("test_profiles", [])
    if not isinstance(profiles_raw, list):
        raise ProductManifestError("test_profiles must be a list")
    test_profiles: list[TestProfile] = []
    for item in profiles_raw:
        if not isinstance(item, dict):
            raise ProductManifestError("each test profile must be a mapping")
        profile_id = _string(item.get("id"), "test_profiles[].id")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", profile_id):
            raise ProductManifestError(f"invalid test profile id: {profile_id}")
        suites = _tuple_strings(item.get("suites"), f"test_profiles.{profile_id}.suites")
        targets = _tuple_strings(item.get("deployment_targets"),
                                 f"test_profiles.{profile_id}.deployment_targets")
        if not suites or not targets:
            raise ProductManifestError(f"test profile {profile_id} needs suites and deployment targets")
        test_profiles.append(TestProfile(
            profile_id, _string(item.get("title"), f"test_profiles.{profile_id}.title"),
            suites, targets,
        ))
    if len({item.id for item in test_profiles}) != len(test_profiles):
        raise ProductManifestError("test profile ids must be unique")

    cli_raw = raw.get("cli", {})
    if not isinstance(cli_raw, dict):
        raise ProductManifestError("cli must be a mapping")
    cli: dict[str, Path] = {}
    for name, relative in cli_raw.items():
        entry_name = _string(name, "cli entry name")
        entry_path = Path(_string(relative, f"cli.{entry_name}"))
        if entry_path.is_absolute() or ".." in entry_path.parts:
            raise ProductManifestError(f"cli.{entry_name} must be a relative product path")
        resolved = (package_root / entry_path).resolve()
        if package_root not in resolved.parents:
            raise ProductManifestError(f"cli.{entry_name} escapes product package")
        if not resolved.is_file():
            raise ProductManifestError(f"cli.{entry_name} does not exist: {entry_path}")
        cli[entry_name] = entry_path

    license_raw = raw.get("license")
    license_descriptor = None
    if license_raw is not None:
        if not isinstance(license_raw, dict):
            raise ProductManifestError("license must be a mapping")
        product_code = _string(license_raw.get("product_code"), "license.product_code")
        allowed = _tuple_strings(license_raw.get("allowed_versions", versions), "license.allowed_versions")
        license_descriptor = LicenseDescriptor(product_code, allowed)

    source_raw = raw.get("source")
    source_root = None
    if source_raw is not None:
        if not isinstance(source_raw, dict):
            raise ProductManifestError("source must be a mapping")
        default = _string(source_raw.get("default"), "source.default")
        env_name = source_raw.get("env")
        if env_name is not None and (not isinstance(env_name, str) or not env_name.isidentifier()):
            raise ProductManifestError("source.env must be an environment variable name")
        configured = os.environ.get(env_name, default) if env_name else default
        candidate = Path(configured).expanduser()
        source_root = (candidate if candidate.is_absolute() else package_root / candidate).resolve()

    provider_path = None
    if raw.get("provider") is not None:
        provider_relative = Path(_string(raw.get("provider"), "provider"))
        if provider_relative.is_absolute() or ".." in provider_relative.parts:
            raise ProductManifestError("provider must be a relative product path")
        resolved_provider = (package_root / provider_relative).resolve()
        if package_root not in resolved_provider.parents or not resolved_provider.is_file():
            raise ProductManifestError(f"provider does not exist: {provider_relative}")
        provider_path = provider_relative
    elif (package_root / "provider.py").is_file():
        provider_path = Path("provider.py")

    return ProductManifest(
        id=product_id,
        title=title,
        plugin_api=plugin_api,
        regression_sdk=raw.get("regression_sdk"),
        package_root=package_root,
        versions=tuple(versions),
        capabilities=capabilities,
        actions=tuple(actions),
        cli=cli,
        license=license_descriptor,
        source_root=source_root,
        test_profiles=tuple(test_profiles),
        provider_path=provider_path,
    )


def discover_products(root: Path) -> dict[str, ProductManifest]:
    """Discover valid packages and reject ambiguous product or License IDs."""
    if not root.is_dir():
        return {}
    discovered: dict[str, ProductManifest] = {}
    license_codes: dict[str, str] = {}
    errors: list[str] = []
    for package_root in sorted(item for item in root.iterdir() if item.is_dir() and not item.name.startswith(".")):
        if not (package_root / "product.yaml").exists():
            continue
        try:
            manifest = load_manifest(package_root)
        except ProductManifestError as exc:
            errors.append(str(exc))
            continue
        if manifest.id in discovered:
            errors.append(f"duplicate product id: {manifest.id}")
            continue
        if manifest.license is not None:
            owner = license_codes.get(manifest.license.product_code)
            if owner is not None:
                errors.append(
                    f"duplicate license product code: {manifest.license.product_code} "
                    f"({owner}, {manifest.id})"
                )
                continue
            license_codes[manifest.license.product_code] = manifest.id
        discovered[manifest.id] = manifest
    if errors:
        raise ProductManifestError("; ".join(errors))
    return discovered
