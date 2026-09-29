"""部署拓扑/状态查询的驱动分发层。

平台核心只负责按环境记录选择部署驱动；具体的部署引擎知识
（pgclusterlib、复制模型 schema 等）由驱动模块持有。
环境记录的 ``deployment_driver`` 字段选择驱动：

- 缺省或 ``"pgcluster"`` —— 内置 :mod:`.pgcluster_topology` 驱动；
- 其他值视为可导入模块路径（如 ``products.myprod.deployment``），
  产品包借此接入自有部署引擎而无需改动平台代码。
驱动模块契约：``topology(settings, environment) -> dict`` 与
``status(settings, environment) -> dict``。
"""

import importlib

from .config import Settings


def _driver(environment: dict):
    name = environment.get("deployment_driver") or "pgcluster"
    if name == "pgcluster":
        from . import pgcluster_topology
        return pgcluster_topology
    return importlib.import_module(name)


def configured_topology(settings: Settings, environment: dict) -> dict:
    return _driver(environment).topology(settings, environment)


def observed_status(settings: Settings, environment: dict) -> dict:
    return _driver(environment).status(settings, environment)
