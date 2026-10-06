# 公司产品公共管理平台

面向数据库产品的部署、回归验证、报告、License 与数据库管理工作台。公共服务和 SDK 位于 backend，产品知识集中在 products；数据库管理使用统一 Studio。

## 运行

```bash
./web.sh setup
./web.sh build
./web.sh start                 # 默认 8080
./web.sh status
./web.sh logs
./web.sh stop
```

依赖只声明在 pyproject.toml，uv.lock 固定解析版本；安装执行 uv sync --frozen --extra dev。前端构建同步生成产品注册和 API 类型。

## 目录职责

```text
backend/platform_app/       公共服务、应用服务、文件控制面与任务执行
backend/platform_regress/   产品无关回归 SDK
frontend/                  公共界面与浏览器验收
products/<产品>/           产品声明、模板、领域用例与前端扩展
cli/                       平台 CLI 和验证入口
tests/                     公共服务和 SDK 测试
docs/                      当前设计、SDK 契约与实施状态
data/                      持久控制面，必须备份
  environments/            环境登记
  profiles/<环境>/         部署配置与测试夹具上下文
  resources/<产品>/<环境>/  外部资源回收账本
  regression/<产品>/<环境>/ 失败重跑记录、执行历史、稳定性状态
  tasks/                   任务事实和分段事件
  deployment-*/            草稿、不可变计划、已审阅申请
runtime/                   文件队列、PID、进程锁，停服务后才能清理
logs/                      Web 和按任务 ID 保存的执行日志
output/<产品>/<环境>/runs/<执行ID>/
  run.json                 本次执行身份
  cases/<用例目标>/         结果、步骤、报告及证据
  suite-result.json        本次聚合
  report.html + junit.xml   导出报告
```

源码目录不保存运行产物；output 不保存连接凭据、失败重跑状态或资源归属。清理报告不会丢失资源回收账本。日志、锁、队列与事实事件采用不同的保留规则；运行中不能删除锁文件。

默认根目录为项目下 data、runtime、logs、output，分别可通过 PRODUCT_PLATFORM_DATA_DIR、PRODUCT_PLATFORM_RUNTIME_DIR、PRODUCT_PLATFORM_LOGS_DIR、PRODUCT_PLATFORM_OUTPUT_DIR 配置。目标实例 PGDATA 属于数据库主机，不属于这些耗材目录。

## 验证

```bash
./cli/check.sh quick
./cli/check.sh full
(cd frontend && npm run build)
git diff --check
```

## 接入与边界

- [设计](docs/design.md)：公共能力、产品边界和任务协议。
- [SDK](backend/platform_regress/SDK.md)：公开类型、生命周期和证据。
- [Demo](products/demo/README.md)：最小产品接入示例。
- [当前实施状态](docs/progress.md)。

不保留旧目录映射、旧 CLI 别名或报告镜像。读取判定只使用结构化事实，不从展示文本推断成功；AI 不改变确定性判定。编辑前核对工作区已有改动，不批量重置仓库，不删除实际数据库目录。

## 工作区

- 数据库部署管理：新建、自动接管、导入配置、审阅变更与继续原执行计划。
- 数据库管理：统一 Studio；运维入口查看会话、锁、复制与参数。
- 数据库压测：pgbench／JDBC 实测指标、阈值和任务取消。
- 平台运营：流水线、定时执行、通知、产品包版本与知识检索。

[当前功能与验收边界](docs/current-functional-status.md)记录已实现行为、实测范围和明确限制。运营定义、知识索引、产品产物及安装计划属于 data 持久控制面；产品包安装锁和前端构建锁属于 runtime。

回归用例开发和报告 review 请遵循[回归用例编写规范](docs/regression-case-authoring.md)。
