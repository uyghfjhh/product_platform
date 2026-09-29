# fbasecman 回归产品知识（vendored）

本目录保存 fbasecman 回归的**产品知识**：套件用例清单（manifest）、执行器断言（executors/domains）、产品运行时方法库（runtime）、配置胶水（cmanconf）与测试资产（assets、JDBC 驱动）。

> 用例的调度、生命周期、判定、证据与报告由平台引擎 `backend/platform_regress` 持有。
> 本目录不再是独立执行框架——`run.sh`、`suite.py`、`plugin.py`、`registry.py`、
> vendored 单测与 Web 控制台已随迁移退役删除。

## 执行入口

```bash
# 平台 CLI（仓库根）
python3 -m platform_regress.cli \
    --product-dir products/fbasecman \
    --output-dir <输出目录> \
    --context-json '{"legacy_source": "<本目录绝对路径>",
                     "legacy_override": "<环境 override.yaml>",
                     "legacy_report_root": "<报告根>"}' \
    <suite.case>
```

或通过平台 Web/队列入口下发 `tests.fbasecman` 任务。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `suites/ha_commands` | 高可用控制台命令与持久化（60 executor 用例 + JDBC 资产） |
| `suites/high_availability` | 高可用故障切换与 monitor 探测（10 executor 用例） |
| `suites/handover` | 转测文档业务用例（56 executor 用例） |
| `suites/global_cache` | 全局 PreparedStatement 缓存（18 executor 用例） |
| `suites/stable` | 常稳压测工具（`stable.sh` 入口，独立于回归目录） |
| `suites/*/assets` | 原生用例引用的探针与 JDBC 源码资产 |
| `env/` | 环境部署/清理辅助（产品侧环境知识） |
| `lib/` | 报告与 rw_toggle 类型辅助 |
| `tools/` | `web_reports.py` 报告解析器 + stable 工具链 |
| `cmanconf.py` | 产品配置加载与 profile 隔离校验 |
| `fbasecman_ops.py` | executor 的 `ops.*` 转发 facade |
| `regress.yaml` | 产品默认回归配置 |

## 已 native 化的套件

`common`、`guc`、`outstanding`、`sql_parse`、`rw_toggle`、`tmp` 的用例已改写为平台原生 `RegressionCase`（见 `products/fbasecman/native.py`、`ha_native.py`、`common_native.py`），本目录仅保留它们引用的 `assets/` 文件。

## 常稳测试

```bash
./stable.sh run <target>
./stable.sh top
```

stable 是独立的常稳压测工具，不走回归目录与平台调度。
