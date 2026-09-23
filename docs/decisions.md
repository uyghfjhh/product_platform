# 当前设计决策

## 部署归属

新平台的数据库部署统一调用 `pgcluster`。旧 fbasecman 框架的 `env setup/start/stop/heal` 不再作为平台部署入口。旧测试代码只保留用例、断言、报告和产品操作逻辑。

原因：部署拓扑、实例生命周期、流复制、MMR、Citus 和数据目录归属应由一个工具管理。平台只负责产品上下文、操作计划、任务事件和 Web 交互。

## fbasecman 迁移边界

fbasecman 回归配置拆为两部分：

- `pgcluster.yaml`：主机、安装、实例、端口、PGDATA、流复制、MMR 和扩展。
- `regress.override.yaml`：旧用例运行所需的数据库端口和平台输出路径。

数据库角色、测试表、`test_db`、MMR 业务节点和 `test_context.yaml` 属于测试夹具，由平台的独立 fixture 步骤准备。它们不塞进 pgcluster 的通用部署模型。

## 结果和证据

任务事件表达当前执行过程；fbasecman 原始 `report.txt`、`summary.json`、`steps.json` 和日志保留在环境专属目录。页面不从中文标题猜测 PASS/FAIL，报告解析只用于展示和回放。

## License

首版只实现 Python 生成和下载。旧 C 工具用于兼容性验证，不作为平台运行依赖；旧后台服务、PostgreSQL 申请表、审批和生成队列不迁移。密钥管理是下一阶段的独立功能。

## 运行形态

单机优先：FastAPI、Huey consumer、SQLite 和本地证据目录。局域网内部使用，保留目标检查、危险操作确认、口令不写日志等基本约束；复杂 SSO/RBAC/多租户不进入首版。

## 失败分类

- `FAILED`：产品测试已执行，但断言或工具返回失败。
- `BLOCKED`：前置条件不足，测试尚未进入业务验证。
- `RECOVERY_REQUIRED`：修改型操作中断，外部环境状态还需核对。
- 平台适配错误优先在平台日志和事件中显示，不伪装成产品用例 PASS。
