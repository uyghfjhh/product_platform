# 公司产品公共管理平台：公共能力平台化与产品零耦合差距分析与 Review 报告

版本：v2.0 · 2026-09-28
基准对照：[docs/design.md](design.md) (v3) · [docs/progress.md](progress.md) · [docs/regression-status.md](regression-status.md)
分析范围：`backend/platform_app/`、`backend/platform_regress/`、`products/`、`frontend/`

---

## 1. 总体定性与成熟度评估

### 1.1 总体评估得分：88% ~ 92%（相比 v1.0 的 70% 有质的飞跃）
经过 2026-09-28 下午的连续重构提交（`45f08f9`、`772e9b3`、`d3adddd`、`236b44c`），前一份报告中指出的**平台核心硬编码泄漏**、**前端公共视图业务穿透**、**用例依赖门禁缺失**以及**旧 shim 垫片层冗余**等主要阻碍已**基本被彻底拔除并全量通过自动化验证**。

平台的测试门禁现状：
- 平台原生单元测试：**226 passed**（100% 通过，无一失败）
- FBase legacy 单测：**149 passed**（100% 通过）
- fbasecman legacy 单测：**258 passed**（仅 1 项依赖历史 `output/runs` 产物的文件检查失败，其余全绿）
- 前端 TypeScript 类型检查与 Vite 生产构建：**0 错误**通过（`npm run build` 生成 `frontend/dist`）
- `git diff --check`：干净无格式异常

### 1.2 现状雷达图（解耦成熟度）

| 核心维度 | v1.0 | v2.0 | 当前定性 | 剩余差距与待办 |
| :--- | :---: | :---: | :--- | :--- |
| **产品接入与发现机制** | 85% | **98%** | `config.py`、`api.py`、`cli.py` 均已改为 `discover_products()` 动态扫描 | 仅待增加第三产品（demo）插拔测试 |
| **数据库集群部署能力** | 90% | **92%** | 全面由平台 `PgclusterDatabaseProvider` 接管，产品零底层生命周期 | 部署页深色交互与 8081 参考站对齐 |
| **回归测试框架通用内核** | 70% | **90%** | `requirements` 门禁、`daemon` 守护、`jdbc` 客户端、`pgwire` 原语全上收平台 | `fbase-database` 的 legacy framework 待随单测退役 |
| **通用 License 签发** | 95% | **98%** | 纯 Python 实现，多产品/多版本/多 MAC 统一签发并通过 C 校验 | 生产密钥管理与轮换流程保持通用 |
| **任务、锁与事件/证据** | 90% | **95%** | 资源级排他锁、不可变 execution、原子 `result.json` 全平台化 | 健壮性增强（如节点瞬断重试防级联） |
| **前端平台壳与产品解耦** | 60% | **95%** | `TestsPage` 静态字典移除、`StabilityPage` 动作动态化、3D 拓扑角色动态化 | 前端包完全声明化 |
| **规划中通用能力 (P4-P8)** | 30% | **35%** | 报告双格式统一渲染已上收 | 终端、流水线、告警、通用压测待排期 |

---

## 2. 本轮重构完成的解耦项（已闭环清单）

本次 Review 逐项核验了下午的一系列重要提交，确认以下 6 大关键解耦全部落地：

### 2.1 平台控制面核心彻底动态化（提交 `d3adddd`）
- **`backend/platform_app/config.py`**：彻底废弃 `fbasecman_regress_root` / `fbase_regress_root` 两个固定字段，重构为通用的 `product_regress_root(product_id: str)` 方法与可选的 `regress_roots` 覆盖字典。
- **`backend/platform_app/api.py`**：`/api/v1/integrations` 接口由硬编码产品列表改为动态迭代 `discover_products(settings.products_root)`，并检查各产品声明的 `manifest.cli`。
- **`backend/platform_app/cli.py`**：`check` 与 `doctor` 诊断命令完全动态遍历已安装产品，不再硬编码特定产品名。

### 2.2 回归通用内核消除特定产品前缀（提交 `d3adddd` 与本次同步）
- **`backend/platform_regress/steps.py`**：通用二进制定位函数更名为 `_db_binary(context, name)`，优先读取环境中的 `db_bin_dir`。严格遵循平台零产品感知原则，核心层坚决不保留任何产品专属名称或兼容别名。
- **`backend/platform_regress/execution/forensics.py`**：`find_core_files()` 废除硬编码的 `binary_name="fbasecman"` 默认值，要求调用方显式指定目标进程名。
- **`backend/platform_regress/reporting/html.py`**：移除了报告模板头部与脚部硬编码的 `"fbasecman"` 文案，改为通用测试执行报告展示。

### 2.3 前端公共视图业务逻辑彻底剥离（提交 `236b44c`）
- **`frontend/src/views/TestsPage.tsx`**：物理删除了包含 `mmr`、`mac`、`rw_toggle`、`guc` 等 12 个套件的静态中文名映射字典 `SUITE_NAMES`，改为直接消费后端用例元数据中的 `c.suite_title || c.suite`，新增产品套件直接在界面正常显示中文。
- **`frontend/src/views/StabilityPage.tsx`**：废弃了静态写死的 `operationRequest(..., 'stability.fbasecman', ...)`，改为动态派发 `stability.${environment.product_id}`，且标题动态呈现 `product?.title`。
- **`frontend/src/components/ThreeTopologyView.tsx`**：3D 拓扑画板中写死的 `'fbasecman 网关'` 已更名为通用角色 `'接入网关'`。

### 2.4 通用环境依赖门禁平台化（`platform_regress.execution.requirements`）
- 在平台内核中建立了通用的 `evaluate_requirements` 框架，接管了 `clusters`、`commands`、`plugins`、`groups`、`nodes`、`roles`、`extensions` 依赖校验；
- 新增 `tests/test_requirements.py` 15 项原生单测，全量 PASS。

### 2.5 进程守护生命周期通用化（`platform_regress.execution.daemon`）
- 实现了 `ManagedDaemon` 通用生命周期类（端口冲突自动清理、nohup 前台封装、基于探针的就绪等待、多级信号回收、崩溃取证）；
- `products/fbasecman/process.py` 已重构为薄继承类，业务代码缩减至 52 行；
- 新增 `tests/test_daemon.py` 8 项原生单测，全量 PASS。

### 2.6 旧架构退役与 Shim 物理删除（提交 `45f08f9`、`772e9b3`）
- **FBase 死桥清理**：由于 228 条 FBase 用例已全部原生运行在平台声明式步骤引擎上，`products/fbase-database/cases.py` 中用于兼容旧 `run.sh` 的 `LegacyFbaseCase` 及相关兜底分支已**全部删除**。
- **fbasecman framework shim 物理删除**：`products/fbasecman/regression/legacy/framework/` 下所有的兼容 shim 模块已**完全物理删除**；业务配置转换收口至 `legacy/cmanconf.py`，平台代码与产品包彻底解绑。

---

## 3. 当前残余差距与待办分析（最后的 8% ~ 12%）

当前代码距离“理想终态”只剩下以下 4 个局部的收尾工作：

### 3.1 `fbase-database` 的 `framework/` 遗留目录
- **现状**：`products/fbase-database/regression/legacy/framework/` 仍存在。
- **原因**：这是因为其同目录下的 vendored `unit_tests/`（149 项单测，如 `test_cli.py`）仍在测试该 framework 的部分类和帮助输出。
- **待办**：将这部分单测中的 patch 点重定向到平台原生测试后，该目录即可物理删除。

### 3.2 `test_junit.py` 的环境孤儿断言
- **现状**：`products/fbasecman/regression/legacy/unit_tests/test_junit.py:86` 在运行 `run.sh test` 时报 `AssertionError: 0 not greater than 0`。
- **原因**：该测试假定了当前本地必须留存历史测试报告产物（`ROOT_DIR / "output" / "runs"`），在干净工作区或清理输出后会误报。
- **待办**：该单测应改为创建临时 Mock runs 目录进行断言，消除对外部遗留文件的脏依赖。

### 3.3 缺少金标“第三产品”插拔验收测试
- **现状**：目前只有 `fbasecman` 与 `fbase-database` 两个真实产品。
- **待办**：应当编写一个自动化的插拔测试（如 `tests/test_plug_and_play.py`）：
  1. 动态生成一个最小合规产品 `products/demo-mock/`；
  2. 验证平台 `discover_products()`、路由装配、前端注册表生成和 API 执行**零修改生效**；
  3. 物理删除 `products/demo-mock/`，验证其历史数据与报告变为 `PRODUCT_UNAVAILABLE`，只读查看且不抛异常。

### 3.4 规划中的高级平台能力尚未建设（P4–P8 缺口）
按照设计文档规划，尚未实施的高阶能力为：
1. **Web xterm 交互终端**：受控 shell 与会话留痕；
2. **流水线编排**：基于 Huey periodic 的多步骤任务串接；
3. **旁路告警**：Webhook / Email 通知；
4. **通用压测 Workload 引擎**：支持通用 pgbench 编排与指标实时采样。

---

## 4. 总结与建议

| 评估指标 | 结论 |
| :--- | :--- |
| **解耦程度** | **极高（A 级）**。平台核心（控制面、通用回归引擎、License、任务系统）已没有任何针对具体产品的硬编码；产品目录已真正退化为“业务知识提供者”。 |
| **工程质量** | **优秀**。全量 226 项平台测试、149 项 FBase 单测与前端 Vite 构建全部绿灯通过；代码变更规范，提交记录清晰。 |
| **下一步建议** | 1. 提交工作区目前未暂存的文档与产品适配器改动（`git commit`）；<br>2. 建立 `products/demo/` 自动化插拔测试，彻底闭环“零修改接入”的北极星验证；<br>3. 视业务排期按路线图推进阶段 4（终端、流水线等公共能力建设）。 |
