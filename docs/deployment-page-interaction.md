# 部署页交互与数据库管理

部署页按“选择环境 → 集群操作 → 集群拓扑”组织。环境选择与新增／编辑集中在顶部，空环境也能登记。常用部署、启动、停止、重启直接显示；体检、自愈、重置和清理收在“更多操作”，沿用操作确认。测试绑定由测试页处理，部署页不再显示“环境用途”。

集群拓扑只展示现有平面视图，隐藏 3D 切换，界面不出现 GSAP／SVG／Three.js 等框架名。节点数量与在线／已停止／未知状态在图上方汇总，状态提示不再遮住节点。点击节点打开详情／节点操作／SQL 抽屉；底部不再展示整份部署 YAML，也不再为页面自动请求该文件。

数据库管理是单独的导航页，切换环境后提供已有的节点选择、对象浏览和 SQL 工作台。当前仍是基础功能，尚未集成完整第三方管理器。

**选定方向（待实施）**：`@prisma/studio-core`（Apache-2.0，React 18/19 兼容）。Prisma Studio 本体的嵌入式 React 组件：schema 树浏览、表数据分页/筛选/排序/行编辑、关联查看、SQL 编辑器＋操作日志，覆盖管理平台需要的主要面。比 iframe 外挂（pgweb/DbGate/CloudBeaver/pgAdmin）更贴合：连接走环境登记，认证与审计天然继承，无需第二套连接配置。

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
