# 实现进度

更新：2026-09-23

## 已完成

- Python 3.12 后端、React + TypeScript 前端、SQLite 元数据和 Huey 本机任务队列。
- 商务简洁风格工作台：产品/环境上下文、响应式布局、桌面和手机浏览器验收。
- 产品与环境登记，任务状态、取消、事件序列、原始命令日志和当前结果存储。
- `pgcluster` 提供者：配置校验、二维拓扑、节点详情、实际节点状态、校验、创建、启停、重启、清理、故障切换和旧主重建入口。
- fbasecman 回归迁移方案：从现有 `regress.yaml` 和 `regress.local.yaml` 生成隔离的 pgcluster MMR 配置、端口和 PGDATA；不再调用旧 `env setup/start/stop`。
- fbasecman 夹具准备模块：创建 `test_db`、角色、测试表、MMR 业务节点和 `test_context.yaml`。
- fbasecman 用例发现、单例/套件/失败重跑入口，步骤事件桥接，当前结果同步。
- fbasecman 报告查看：步骤、检测项、拓扑快照回放、原始报告、日志搜索和 ERROR/WARN 高亮，JUnit/HTML 导出。
- 数据库 SQL 工作台：一次执行一次 SQL、结果表、对象树、列信息、超时和结果行数限制。
- Python License 生成：读取旧格式加密密钥、Ed25519 签名、Argon2id + XChaCha20-Poly1305 解密、文件下载；没有迁移旧后台服务。
- 单机启动和 `0.0.0.0:8765` 局域网访问。

## 已验证

- 平台 pytest：`9 passed`。
- 前端 TypeScript/Vite：`npm run build` 通过。
- 前端 Playwright smoke：桌面部署拓扑、节点详情、自动化测试清单、License 页面、手机布局通过。
- `pgcluster` 单测：`17 tests OK`。
- 隔离 PostgreSQL/FBase 两节点集群：`25432/25433` 创建、状态、验证和备库重启恢复通过。
- 隔离 fbasecman MMR 集群：`15011-15027` 共 14 个节点启动，`node1/node2 ACTIVE`，`pgcluster verify mmr.fbasecman_regress` 通过；测试夹具准备通过。
- Python 生成的 License 已被旧 `fd_licenser check` 校验器接受，使用临时测试密钥。
- fbasecman 用例执行链路已实际提交一次：`guc.search_path_reuse_sql_parse` 执行 90 秒后 FAIL，平台保存事件和日志；失败说明需要继续调查，不能当作框架通过。

## 当前未完成

- Python License 的 `genkey`、查看密钥、修改口令和密钥版本管理页面尚未实现；当前只读取已有密钥生成 License。
- fbasecman 真实用例失败原因尚未完成定位；当前只看到旧工具汇总 `FAIL`，下一步读取隔离输出目录下的 report、summary 和产品日志。
- 旧 fbasecman 全部套件、稳定性正式时长、真实部署清理和故障切换尚未完成全量验收。
- AI 诊断、知识库索引、代码问答和模板化报告仍是设计阶段。
- 真实数据库 SQL 工作台、多机执行和长期服务管理尚未完成验收。

## 数据与进程

- 平台服务：`http://192.168.0.12:8765`，当前监听 `0.0.0.0:8765`。
- 平台 SQLite：`data/platform.sqlite3`；队列：`data/queue.sqlite3`。
- 平台隔离演示集群配置：`tests/assets/pgcluster-smoke.yaml`，PGDATA 在 `data/smoke/`，端口 `25432/25433`。
- fbasecman 隔离环境配置：`data/profiles/cman-lab/`，PGDATA 在 `data/cman-lab-pgdata/`。
- fbasecman 当前结果和报告：`data/legacy_cman/<environment_id>/output/`。
- 停止平台服务不会自动停止 pgcluster 管理的数据库；停止数据库应从部署页提交明确的 pgcluster 操作。

## 下次接续

1. 读取 `data/legacy_cman/cman-lab/output/runs/guc/search_path_reuse_sql_parse/` 的 report、summary、fbasecman 日志和数据库日志，定位失败原因。
2. 修复或补齐新配置与旧用例所需的运行参数，重跑该用例，再验证一条高可用和一条路由用例。
3. 补 Python License 密钥管理（生成密钥、查看公钥、修改口令），先做文件格式兼容测试。
4. 增加平台级节点日志、复制状态和 MMR 成员状态面板。
5. 完成 fbasecman 套件批量运行、失败聚合和正式报告模板。
