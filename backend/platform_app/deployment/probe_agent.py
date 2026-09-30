"""Read-only probe; also sent verbatim to SSH hosts with their Python 3."""

import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def inspect_installation(home, sources):
    root = Path(home)
    tools = {
        name: (root / "bin" / name).is_file()
        and os.access(root / "bin" / name, os.X_OK)
        for name in ("postgres", "pg_ctl", "psql", "pg_basebackup", "pg_config")
    }
    version = ""
    if tools["postgres"]:
        result = subprocess.run(
            [str(root / "bin/postgres"), "--version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
    versions = {}
    for name, exists in tools.items():
        if exists:
            output = subprocess.run(
                [str(root / "bin" / name), "--version"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            match = (
                re.search(r"(\d+)\.\d+", output.stdout)
                if output.returncode == 0
                else None
            )
            versions[name] = match.group(1) if match else None
    complete = (
        all(tools.values())
        and bool(version)
        and len(set(versions.values())) == 1
        and None not in versions.values()
    )
    locations = {}
    if tools["pg_config"]:
        for field, flag in (
            ("bin_dir", "--bindir"),
            ("shared_dir", "--sharedir"),
            ("library_dir", "--pkglibdir"),
        ):
            output = subprocess.run(
                [str(root / "bin/pg_config"), flag],
                capture_output=True,
                text=True,
                timeout=3,
            )
            value = output.stdout.strip()
            if output.returncode == 0 and value.startswith("/"):
                locations[field] = value
    return {
        "home": str(root),
        "sources": sources,
        "version": version,
        "tools": tools,
        "tool_major_versions": versions,
        "complete": complete,
        **locations,
    }


def run(request):
    if request["operation"] == "discover":
        found = {}

        def candidate(binary, source):
            binary = Path(binary).resolve()
            if binary.name == "postgres":
                home = str(binary.parent.parent)
                found.setdefault(home, []).append(source)

        if request.get("home"):
            candidate(Path(request["home"]) / "bin/postgres", "用户指定")
        binary = shutil.which("postgres")
        if binary:
            candidate(binary, "PATH")
        for pattern in (
            "/usr/local/*/bin/postgres",
            "/opt/*/bin/postgres",
            "/usr/lib/postgresql/*/bin/postgres",
        ):
            for binary in glob.glob(pattern)[:16]:
                candidate(binary, "常见安装目录")
        for path in glob.glob("/proc/[0-9]*/exe"):
            try:
                binary = os.readlink(path)
                if Path(binary).name == "postgres":
                    candidate(binary, "运行进程")
            except OSError:
                pass
        if request.get("data_dir"):
            path = Path(request["data_dir"]) / "postmaster.opts"
            if path.is_file():
                args = shlex.split(path.read_text())
                if args:
                    candidate(args[0], "历史启动参数")
        installations = []
        for home, sources in list(found.items())[:16]:
            try:
                installations.append(inspect_installation(home, sorted(set(sources))))
            except (OSError, subprocess.TimeoutExpired) as exc:
                installations.append(
                    {
                        "home": home,
                        "sources": sources,
                        "version": "",
                        "complete": False,
                        "error": str(exc),
                    }
                )
        return {"installations": installations}

    checks = []
    install = request["installation"]
    installation = inspect_installation(install["home"], ["当前方案"])
    checks.append(
        {
            "title": "数据库工具完整性",
            "ok": installation["complete"],
            "detail": installation,
        }
    )
    root = Path(install["home"])
    for plugin in install.get("plugins", {}):
        required = install["plugins"][plugin]
        extension = required.get("extension", plugin)
        library = required.get("preload_library")
        available = (
            Path(installation.get("shared_dir", str(root / "share")))
            / "extension"
            / (extension + ".control")
        ).is_file()
        if library:
            available = (
                available
                and (
                    Path(installation.get("library_dir", str(root / "lib")))
                    / (library + ".so")
                ).is_file()
            )
        checks.append({"title": "扩展 " + plugin, "ok": available, "detail": str(root)})
    license_file = (install.get("license") or {}).get("source_file")
    if license_file:
        checks.append(
            {
                "title": "License 文件",
                "ok": Path(license_file).is_file() and os.access(license_file, os.R_OK),
                "detail": license_file,
            }
        )
    match = re.search(r"(\d+)\.\d+", installation["version"])
    major = match.group(1) if match else None
    for node in request["nodes"]:
        path = Path(node["data_dir"])
        nonempty = path.exists() and (
            not path.is_dir() or next(path.iterdir(), None) is not None
        )
        pg_version = (
            (path / "PG_VERSION").read_text().strip()
            if (path / "PG_VERSION").is_file()
            else None
        )
        # .pgcluster-managed 是引擎写下的受管断点：部分完成的部署可重放幂等
        # create 恢复，受管节点按既有实例口径校验而不是当作待创建空目录。
        # 标记内容为 {"node": 名称}，归属不匹配时按未受管处理。
        managed = False
        marker = path / ".pgcluster-managed"
        if marker.is_file():
            try:
                claim = json.loads(marker.read_text() or "{}").get("node")
            except (OSError, ValueError):
                claim = None
            managed = claim in (None, node["name"])
        existing = managed or node.get(
            "existing", request["mode"] not in {"new", "free"}
        )
        checks.append(
            {
                "title": node["name"] + " 数据目录",
                "ok": bool(pg_version == major) if existing else not nonempty,
                "detail": {
                    "path": str(path),
                    "canonical_path": str(path.resolve()),
                    "nonempty": nonempty,
                    "managed": managed,
                    "pg_version": pg_version,
                },
            }
        )
        if not existing:
            probe = socket.socket()
            try:
                probe.bind(("0.0.0.0", node["port"]))
                ok, detail = True, "端口可分配（执行前会重新检查）"
            except OSError as exc:
                ok, detail = False, str(exc)
            finally:
                probe.close()
            checks.append(
                {
                    "title": node["name"] + " 端口 " + str(node["port"]),
                    "ok": ok,
                    "detail": detail,
                }
            )
            parent = path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            checks.append(
                {
                    "title": node["name"] + " 目录权限",
                    "ok": parent.is_dir() and os.access(parent, os.W_OK | os.X_OK),
                    "detail": str(parent),
                }
            )
    return {"checks": checks}


if __name__ == "__main__":
    try:
        print(json.dumps(run(json.loads(sys.argv[1])), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
