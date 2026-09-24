# 新增回归套件

套件只需要向注册中心提供一个 `SuitePlugin`。CLI 的 `show/run` 和 Web 用例列表都从同一个插件读取元数据。项目当前运行在 Python 3.6，新增代码不要使用 3.7 之后才进入标准库的功能。

## 最小目录

```text
suites/example/
├── __init__.py
├── case.py
├── manifest.py
├── suite.py
└── plugin.py
```

`case.py` 中的用例继承 `framework.suites.CaseSpec`，`name` 在套件内唯一。领域参数可以保留在子类中。`target` 由 `suite_id` 和 `name` 自动组成，不需要每个用例重新实现。

```python
from framework.suites import CaseSpec


class ExampleCase(CaseSpec):
    def __init__(self, name, summary):
        super(ExampleCase, self).__init__(
            suite_id="example", name=name, summary=summary, executor=name,
        )
```

`manifest.py` 提供无副作用的用例列表；默认可执行列表应排除 `enabled=False` 的用例。`suite.py` 提供 `run(root, target=None)` 和可选的 `show()`。`run` 必须返回布尔值、非负退出码或 `CaseResult`；成功返回 `True` 或 `0`，失败返回 `False`、非零退出码或抛出异常。不要省略返回值，否则会被判为失败。单用例执行时，`target` 是点号后的用例名。

```python
from framework.suites import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="example",
    title="示例套件",
    description="示例业务能力",
    case_loader=case_items,
    runner=run,
    shower=show,
)
```

最后将 `suites.example.plugin` 加入 `suites/registry.py` 的 `PLUGIN_MODULES`，并在 `unit_tests/test_suite_registry.py` 的预期列表中加入 `example`。验证：

```bash
./run.sh show example
python3 -m unittest discover -s unit_tests -t . -p 'test_*.py'
```

套件运行时产物放在 `output/runs/example/<case>/`，至少写 `summary.json`（含 `target`、`status`、`reason`）和 `report.txt`。环境预检默认尝试恢复环境，恢复或健康检查失败则返回退出码 3；调试时可用 `--preflight warn` 或 `--preflight off`。
