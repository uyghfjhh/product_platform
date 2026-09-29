"""用例 executor 的产品操作面（ops）。

平台路径下 executor 的签名为 ``def case_x(context)``，函数体内的
产品断言/进程操作统一写成 ``ops.<name>(...)``。本模块通过 PEP 562
模块级 ``__getattr__`` 把任意属性/方法访问转发到当前用例的运行时对象：
运行时由 ``RuntimeBinding`` 在 executor 调用前 ``bind`` 进来，执行完
``unbind`` 释放。executor 因此只依赖平台 ``context`` 与本模块，
不再直接持有 runtime 实例。
"""

_current_runtime = None


def bind(runtime):
    """绑定当前用例的运行时对象（由 RuntimeBinding 在执行前调用）。"""
    global _current_runtime
    _current_runtime = runtime


def unbind():
    global _current_runtime
    _current_runtime = None


def runtime():
    """返回当前绑定的运行时对象（executor 需要取 runtime 本体时使用）。"""
    if _current_runtime is None:
        raise RuntimeError("ops: 当前没有绑定的用例运行时")
    return _current_runtime


def __getattr__(name):
    rt = _current_runtime
    if rt is None:
        raise AttributeError(name)
    return getattr(rt, name)
