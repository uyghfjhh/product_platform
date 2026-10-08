"""Check declared runtime packages and imports without loading platform state."""
import importlib
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path


def main():
    if sys.version_info < (3, 12):
        print("需要 Python 3.12 或更新版本，请执行 ./web.sh setup", file=sys.stderr)
        return 1
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    modules = {"PyYAML": "yaml", "psycopg": "psycopg", "argon2-cffi": "argon2", "PyNaCl": "nacl"}
    failures = []
    for dependency in project["project"]["dependencies"]:
        name = re.match(r"[A-Za-z0-9_.-]+", dependency).group()
        try:
            importlib.metadata.version(name)
            importlib.import_module(modules.get(name, name.replace("-", "_")))
        except (ImportError, OSError) as exc:
            failures.append(f"{name}: {exc}")
    if failures:
        print("依赖检查失败：\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print("依赖检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
