# GUC 同步开发前回归

8 个入口：`guc.{extended_boundary,transaction_sync,savepoint_report,backend_redeploy}_{hint,sql_parse}`。
产品业务源码不需要预先增加测试接口；内部检查通过当前 CMake 构建对象与测试链接拦截器生成独立测试代理。

## 执行

在仓库根目录用 `.venv/bin/python`，设置 `PYTHONPATH=backend:.`：

```bash
PYTHONPATH=backend:. .venv/bin/python -m platform_regress.cli \
  --product-dir products/fbasecman \
  --output-dir output/fbasecman/guc-development \
  --state-dir data/regression/fbasecman/guc-development \
  --context-json '<环境 JSON>' guc.extended_boundary_hint
```

环境字段：`fbasecman_bin`（当前 CMake 构建 sources/fbasecman）、`license_dir`、`nodes.mmr1/mmr2` 的 host/port、`extra_nodes.pg_3/pg_4` 的 host/port/application_name、`mmr_group_name`、`psql_bin`、`user`。
两个主节点应有相同的数据库/角色默认值；主备阶段必须有健康 pg_3。

测试只使用现有 postgres 账号，不创建临时用户、不修改HBA；Hint 的 sql_parse 对照使用独立测试代理和同一账号。不会修改被测业务源码，不停止数据库，不修改正式代理配置。内部测试需要原构建的 link.txt、flags.make、对象及静态库，编译/链接证据归档；开发后先正常重建产品，再重跑同一入口。

## 预期

- 开发前两个缺陷与未满足的目标行为报 FAIL，具体到参数、协议或真实缓存事件。
- 开发后所有支持范围内业务及内部检查满足同一断言时可 PASS；没有永久硬编码的内部 BLOCKED。
- 当前配置规则拒绝 Hint/session 和 session/reserve=yes，按已存在的产品限制 SKIPPED 留证，不能默默修改配置或宣称此分支通过。缺数据库、对象文件、连接权限等实际环境前提仍为 BLOCKED/ERROR。
- 复杂混合请求先冻结实际 sql_parse 基线，仅已支持组合要求 Hint 等价；开发后 sql_parse 的响应、错误及值必须与冻结记录一致。基线目录默认 `data/regression/fbasecman/guc-alignment-baselines`，不要在开发后删除以重新接受变化。
- `guc_alignment_scenarios` 可选择本综合场景的子检查或 `product_cache_boundaries`；`guc_alignment_topologies` 可选择 mmr/replication。选择复跑保留未执行分母，不算完整覆盖。

## 取证及故障

`guc-alignment-plan.json`、`guc-alignment-coverage.json` 包含完整检查计划、16 项新增测试/13 项验收映射及实际状态。每步有 expected/actual/assertion/analysis、原始发送和收到的载荷；故障轨迹记录真实 frontend/transaction/backend 缓存、outstanding、pending、队列与注入触发事实。

本地构造/apply/入队失败及 E/Q 预记录、pending、outstanding、转发失败分别在独立测试代理执行。要求注入点确实到达、无本条成功标签、无失败正式提升；本地失败要求断连。超时、没有触发、代理崩溃都不能冒充注入成功。失败点未触发在开发前是 FAIL，不是等业务开发的占位。

测试驱动单测：

```bash
.venv/bin/python -m pytest -q tests/test_fbasecman_guc_alignment.py
```

## 两模式场景控制

Hint 的 report/DISCARD 写侧固定使用事务外完整路由标签后 BEGIN；sql_parse 使用 BEGIN READ WRITE。参数值观测用独立 Q，GUC 执行协议和候选断言仍按声明使用 Q/E。默认值由无额外启动 options 的物理直连读取，不使用平台 SQL 会话的 timeout 默认值。

session pool 不要求物理后端切换；该能力由 transaction pool 分支单独验证。用户已排除 sql_parse 既有“拒绝 DISCARD 后再次 DISCARD/PreparedStatement 复用”组合专项；独立 DISCARD 成功与事务内拒绝/回滚断言仍执行，排除步骤明确记录 SKIPPED。

## 用户确定的单账号范围

同进程双模式交错专项不执行，报告明确标记 SKIPPED；不能宣称两个独立代理证明了同进程 owner 隔离。其余候选、事务、会话A/B/C、故障及重部署检查保持原断言，A/B/C是同一账号的独立连接，不是三个用户。
