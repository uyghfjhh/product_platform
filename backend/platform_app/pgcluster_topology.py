"""pgcluster 部署驱动：拓扑与运行状态的子进程探针。

这是 ``platform_app.topology`` 的默认 ``pgcluster`` 驱动实现——
平台的部署引擎知识（pgclusterlib API、streaming/logical/citus/mmr
复制模型的角色与边推导）集中在此模块，``topology.py`` 只做驱动分发。
新产品接入其他部署引擎时在产品包内提供同构模块（暴露
``topology(settings, environment)`` / ``status(settings, environment)``），
并通过环境记录的 ``deployment_driver`` 字段指到该模块路径。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT, Settings

_DEPLOY_DIR = Path(__file__).parent / "deployment"
TOPOLOGY_PROBE_PATH = _DEPLOY_DIR / "pgcluster_topology_probe.py"
STATUS_PROBE_PATH = _DEPLOY_DIR / "pgcluster_status_probe.py"

TOPOLOGY_SCRIPT = TOPOLOGY_PROBE_PATH.read_text(encoding="utf-8")
STATUS_SCRIPT = STATUS_PROBE_PATH.read_text(encoding="utf-8")


def _deployment(environment: dict) -> tuple[Path, str]:
    path = Path(environment.get("deployment_config") or "")
    target = environment.get("deployment_target")
    if not path.is_file() or not target:
        raise ValueError("请先登记有效的 pgcluster 配置和目标")
    return path, target


def _probe_env(settings: Settings) -> dict[str, str]:
    existing = os.environ.get("PYTHONPATH")
    parts = [str(settings.pgcluster_root), str(ROOT / "backend")]
    if existing:
        parts.append(existing)
    return {**os.environ, "PYTHONPATH": os.pathsep.join(parts)}


def topology(settings: Settings, environment: dict) -> dict:
    path, target = _deployment(environment)
    result = subprocess.run(
        [sys.executable, str(TOPOLOGY_PROBE_PATH), str(path), target],
        cwd=settings.pgcluster_root,
        env=_probe_env(settings),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "无法读取部署拓扑")
    return json.loads(result.stdout)


def status(settings: Settings, environment: dict) -> dict:
    path, target = _deployment(environment)
    try:
        result = subprocess.run(
            [sys.executable, str(STATUS_PROBE_PATH), str(path), target],
            cwd=settings.pgcluster_root,
            env=_probe_env(settings),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("读取节点状态超时") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "读取节点状态失败")
    return json.loads(result.stdout)
