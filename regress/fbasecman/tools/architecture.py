"""Static checks for the target package architecture."""

import ast
from pathlib import Path


AMBIGUOUS_MODULE_NAMES = {"utils", "misc", "legacy", "common_helpers"}

TRANSITIONAL_IMPORTS = {
    "products/fbasecman/environment/provider.py": {
        "env.cluster",
        "env.inventory",
    },
}

TRANSITIONAL_DYNAMIC_SOURCE_FUNCTIONS = {}

TRANSITIONAL_SUBPROCESS_FUNCTIONS = {
    "suites/common/suite.py": {"__init__", "set_locale", "restore"},
    "suites/common/helpers.py": {"__init__", "set_locale", "restore"},
}


def _python_files(root):
    for package in ("framework", "products", "suites"):
        package_root = root / package
        if package_root.exists():
            for path in package_root.rglob("*.py"):
                yield path


def _import_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def architecture_violations(root):
    root = Path(root)
    violations = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            violations.append("%s: syntax error: %s" % (relative, exc))
            continue

        if path.stem in AMBIGUOUS_MODULE_NAMES:
            violations.append("%s: ambiguous module name %r" % (relative, path.stem))

        allowed = TRANSITIONAL_IMPORTS.get(relative, set())
        for imported in _import_names(tree):
            top = imported.split(".", 1)[0]
            if imported in allowed:
                continue
            if relative.startswith("framework/") and top in {
                "products", "suites", "tests", "env", "lib"
            }:
                violations.append("%s: framework imports forbidden %s" % (relative, imported))
            if relative.startswith("products/") and top in {"suites", "tests", "env", "lib"}:
                violations.append("%s: product imports forbidden %s" % (relative, imported))
            if relative.startswith("suites/") and top == "tests":
                violations.append("%s: suite imports legacy tests package %s" % (relative, imported))

        if relative.startswith("suites/"):
            dynamic_source_lines = set()
            allowed_dynamic_functions = TRANSITIONAL_DYNAMIC_SOURCE_FUNCTIONS.get(
                relative, set()
            )
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    function.name in allowed_dynamic_functions
                ):
                    dynamic_source_lines.update(
                        getattr(child, "lineno", -1) for child in ast.walk(function)
                    )
            subprocess_lines = set()
            allowed_subprocess_functions = TRANSITIONAL_SUBPROCESS_FUNCTIONS.get(
                relative, set()
            )
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    function.name in allowed_subprocess_functions
                ):
                    subprocess_lines.update(
                        getattr(child, "lineno", -1) for child in ast.walk(function)
                    )
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.lineno not in subprocess_lines
                    ):
                        violations.append(
                            "%s:%s: suite directly calls subprocess.%s"
                            % (relative, node.lineno, node.func.attr)
                        )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "write_text" and any(
                        isinstance(arg, ast.Name) and "source" in arg.id.lower()
                        for arg in node.args
                    ) and node.lineno not in dynamic_source_lines:
                        violations.append(
                            "%s:%s: suite writes dynamic source text" % (relative, node.lineno)
                        )
    return violations
