"""HA console 命令用例运行时。

本模块历史上同时承担通用用例生命周期、fbasecman 进程管理和 HA 命令
语义校验三层职责，拆分后只保留 HA 套件的领域薄层与兼容导出：

- 通用用例生命周期（目录/journal/报告/套件互斥锁）：
  ``platform_regress.runtime.CaseRuntime``
- fbasecman 产品能力（进程、配置模板、console/业务 psql、配置 diff、
  备份断言、崩溃取证）：``products.fbasecman.case_runtime.FbasecmanCaseRuntime``
- 端口分配：``platform_regress.execution.ports``

外部仍可从本模块导入 ``HaCommandRuntime``/``HaCommandFailure``/
``_port_pair``/``_port_free``/``_non_ephemeral_port_range``/``_json_write``
等历史名字，签名与语义保持不变（包括对模块级私有名的 patch）。
"""

from platform_regress.execution.ports import (
    free_port_block, non_ephemeral_port_range, port_is_free,
)
from platform_regress.persistence.atomic import write_json
from platform_regress.sdk import CaseFailure
from products.fbasecman.case_runtime import (
    FbasecmanCaseRuntime,
)


class HaCommandFailure(CaseFailure):
    """HA 命令用例失败；同时是各套件通用的"用例失败"异常兼容名。"""


class HaCommandRuntime(FbasecmanCaseRuntime):
    """HA console 命令用例运行时。

    通用生命周期与 fbasecman 产品能力全部由基类提供；本类只保留
    ``failure_class`` 绑定，保证失败类型与历史行为一致。
    """

    failure_class = HaCommandFailure

    def report_document_kwargs(self, status, reason):
        """Expose the case's documented test flow before raw command evidence."""
        kwargs = super().report_document_kwargs(status, reason)
        notes = [str(item) for item in getattr(self.case, "notes", ()) if str(item).strip()]
        if notes:
            kwargs["overview_steps"] = notes
        return kwargs


# ----------------------------------------------------------------------
# 兼容导出：以下名字历史上定义在本模块，单测和外部代码仍在引用/patch。
# ----------------------------------------------------------------------

def _port_free(port):
    """端口可绑定探测；patch 本名字会影响 ``_port_pair`` 的选口结果。"""
    return port_is_free(port)


def _non_ephemeral_port_range():
    """内核临时端口段之外的最宽可用区间。"""
    return non_ephemeral_port_range()


def _port_pair(seed):
    """分配 (listen, read) 端口对；内部经模块级名字调用，保持可 patch。"""
    try:
        base = free_port_block(
            seed, 3, is_free=_port_free, port_range=_non_ephemeral_port_range)
        return base, base + 1
    except RuntimeError as exc:
        raise HaCommandFailure(str(exc))


def _json_write(path, value):
    """原子写 JSON 摘要文件（历史上为裸写，现统一走原子替换）。"""
    write_json(path, value)
