# 部署页交互与数据库管理

部署页按“选择环境 → 集群操作 → 集群拓扑”组织。环境选择与新增／编辑集中在顶部，空环境也能登记。常用部署、启动、停止、重启直接显示；体检、自愈、重置和清理收在“更多操作”，沿用操作确认。测试绑定由测试页处理，部署页不再显示“环境用途”。

集群拓扑只展示现有平面视图，隐藏 3D 切换，界面不出现 GSAP／SVG／Three.js 等框架名。节点数量与在线／已停止／未知状态在图上方汇总，状态提示不再遮住节点。点击节点打开详情／节点操作／SQL 抽屉；底部不再展示整份部署 YAML，也不再为页面自动请求该文件。

数据库管理是单独的导航页，切换环境后提供节点选择、对象浏览、SQL 工作台、实例运行状态（会话/锁/复制/参数）与嵌入式数据管理器。

**选定方向（已实施）**：`@prisma/studio-core@0.33.0`（Apache-2.0，React 18/19 兼容，版本精确锁定）。Prisma Studio 嵌入式 React 组件：schema 切换、表数据分页/筛选/排序/行编辑、Visualizer、Console。实施细节：

- 前端 `frontend/src/components/StudioPanel.tsx`：`createStudioBFFClient({url: /api/v1/environments/{id}/studio?port=N, customPayload: {environment_id, port}})` + `createPostgresAdapter` + `<Studio>`；`React.lazy` 懒加载（独立 chunk ~3.6MB gzip 1.1MB，按需加载不拖慢首屏）；节点端口或环境变化经 `key` 重挂载。
- 后端 `backend/platform_app/studio.py`：`POST /api/v1/environments/{id}/studio` 分发 `query`/`sequence`/`transaction`/`sql-lint` 四种 procedure，Either 元组 `[error, result]` 返回；`query-insights` 明确返回未实现。
- 参数翻译 `_translate_parameters`：kysely `$N` → psycopg `%s`，状态机跳过单/双引号、`$tag$` 美元引用、行注释、块注释；按占位符出现序重排参数（`$2` 先于 `$1` 或重复引用时绑定仍正确）。
- 序列化 `_encode_value`：datetime/date/time→ISO、Decimal→str、int8 超 JS 安全整数→str、bytea→`\x` hex、`transformations: {col: "json-parse"}` → JSON 字符串。
- `schema` → `set_config('search_path', %s)` 参数化绑定，无注入面。
- `sql-lint` = `EXPLAIN` 解析/规划级校验，事务内执行强制回滚（`EXPLAIN ANALYZE` 也不落盘）；`statement_position` 减 `EXPLAIN ` 前缀长度映射回原 SQL 偏移。
- `transaction` 非 autocommit 全量执行后 commit，任一步失败 rollback 返回错误元组；`sequence` 双查询逐条容错按 `[[err,res],[err,res]]` 返回。
- 浏览器验收 `frontend/tests/studio-panel-check.mjs`：真实环境挂载、BFF 全 200、切换节点 BFF 指向新端口、零页面错误。

接入设计（已核实协议，可落地）：

- 前端：`createStudioBFFClient({url: /environments/{id}/studio})` + `createPostgresAdapter` + `<Studio>`，`React.lazy` 懒加载（包含 elkjs/d3，包约 44MB 解压，挂数据库页按需加载）；`customPayload` 可带环境上下文。
- 后端：BFF 协议为 JSON POST，`procedure` 分发 `query`/`sequence`/`transaction`/`sql-lint`，响应体即 Either 元组 `[null, rows]` 或 `[{name,message}]`。用 FastAPI+psycopg 实现即可（约百余行），不需要 Node sidecar。
- 落地注意：`query.parameters` 按 `$N` 占位，需翻译成 psycopg 的 `%s`（要跳过字符串字面量/dollar-quoted 块，小状态机）；datetime 序列化为 ISO；int8 大数按字符串编码防精度丢失；`query.transformations` 标 `json-parse` 的列返回 JSON 字符串；`schema` 字段对应 `set_config('search_path', …)`；`query-insights` 可选不实现（客户端不启用该视图）。
- 风险：0.x 版本 API 可能变动，锁死版本；FBase 自定义类型最多降级为文本展示；sql-lint 基于 EXPLAIN，可选。

候选备选（不选理由存档）：

- [LibreDB Studio](https://github.com/libredb/libredb-studio)：SQL IDE 方向，服务端／Next.js 依赖边界与现有 Vite＋FastAPI 不合。
- [pgAdmin 4](https://www.pgadmin.org/docs/pgadmin4/latest/server_deployment.html)：完整 DBA 工具，只能独立服务＋反向代理，iframe 嵌入受 X-Frame/CSRF 限制，接入割裂。
- pgweb/DbGate/CloudBeaver：iframe 整机方案，功能（pgweb 只读浏览）或体量（CloudBeaver Java 容器）不匹配，且连接配置要与环境登记重复维护。
- 自建（sqlglot＋AG Grid）：工作量不低于 BFF 端点，产出不如 Studio 成熟。

AG Grid 是数据表格组件，不替代这些数据库管理工具；本批没有保留 AG Grid 新依赖。

## 等保环境恢复依据

老回归目录 `/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress_v2` 的 `regress.yaml` 定义三个等保节点：mac_primary(15432)、mac_standby(15433)、logical_subscriber(15434)，目录为 `/home/postgres/pgdata/mac1`、mac2、mac3。`framework/environment.py` 与 `replication.py` 原先直接执行部署／复制准备，并不产出 pgcluster YAML。

当前环境登记与旧 profile 均已缺失。依据这些声明、实际 mac1/postmaster.opts 中的 `/usr/local/fbase15.15` 二进制路径及既有数据目录，生成 `data/profiles/fbase-mac/pgcluster.yaml`，对应目标 logical.fbase_regress：mac 主备组＋mac_subscriber 单节点组，逻辑发布／订阅／槽名仍为 fbase_regress_pub／fbase_regress_sub／fbase_regress_slot。已通过 pgcluster 配置校验和三个实例目标匹配，并恢复 fbase-mac 登记及 mac 测试绑定。

本次只修改控制面登记／配置，未执行启动、创建、清理、SQL 或全套件回归。运行状态探测显示三个节点当前均未在线，恢复登记不表示数据库已经启动；旧测试产物也不会由环境登记自动重建。
