# 部署页交互与数据库管理

部署页按“选择环境 → 集群操作 → 集群拓扑”组织。环境选择与新增／编辑集中在顶部，空环境也能登记。常用部署、启动、停止、重启直接显示；体检、自愈、重置和清理收在“更多操作”，沿用操作确认。测试绑定由测试页处理，部署页不再显示“环境用途”。

集群拓扑只展示现有平面视图，隐藏 3D 切换，界面不出现 GSAP／SVG／Three.js 等框架名。节点数量与在线／已停止／未知状态在图上方汇总，状态提示不再遮住节点。点击节点打开详情／节点操作／SQL 抽屉；底部不再展示整份部署 YAML，也不再为页面自动请求该文件。

## 统一数据库工作区

数据库管理只保留一个 Prisma Studio 工作区，覆盖 schema／表数据／结构浏览、编辑、Visualizer、Console 和 SQL。平台只提供顶部环境选择、目标实例选择、刷新与全屏；没有第二套对象树、SQL 编辑器、结果面板或运行状态分页。

- 部署拓扑的 SQL 入口和节点详情的数据库管理入口进入同一页面；SQL 入口直接打开 Studio 的 SQL 视图。
- 目标以节点 ID 选择，服务端从当前环境拓扑解析主机及端口；同端口的不同主机不会混淆。环境连接走 `/api/v1/environments/{id}/studio`，节点连接走 `/studio/nodes/{node_id}`。不保留任意端口覆盖入口。
- 连接和执行统一在 `backend/platform_app/studio.py`，支持 query／sequence／transaction／sql-lint 协议、类型序列化、参数绑定及错误返回。旧 database.py、手工查询／对象／会话／锁／复制／参数接口与 SQL 抽屉已删除；测试绑定的连接检查也使用同一执行接口。
- 使用 `@prisma/studio-core@0.33.0` 的公开 theme 接口，颜色引用平台 CSS 变量，跟随 cman／dark／soft／warm；不修改第三方源码，不维护另一套主题配置。编辑器模式与平台亮度不同时保证文本可读。
- Studio 懒加载并占据主工作区；环境或节点变化重新创建适配器，刷新重置工作区，全屏支持 Esc。错误边界按连接范围重置，不沿用旧连接错误。
- `frontend/tests/studio-workspace.mjs` 对隔离服务校验单一工作区、四套主题、同端口节点切换、SQL、全屏及移动端。Studio 结果使用原生 PostgreSQL 适配器的模拟元数据，不连接真实数据库。

## 等保环境恢复依据

老回归目录 `/home/postgres/fly_dev/postgresql_for_fbase_dev/fbase_regress_v2` 的 `regress.yaml` 定义三个等保节点：mac_primary(15432)、mac_standby(15433)、logical_subscriber(15434)，目录为 `/home/postgres/pgdata/mac1`、mac2、mac3。`framework/environment.py` 与 `replication.py` 原先直接执行部署／复制准备，并不产出 pgcluster YAML。

该次恢复前，环境登记与旧 profile 均已缺失。依据这些声明、实际 mac1/postmaster.opts 中的 `/usr/local/fbase15.15` 二进制路径及既有数据目录，生成 `data/profiles/fbase-mac/pgcluster.yaml`，对应目标 logical.fbase_regress：mac 主备组＋mac_subscriber 单节点组，逻辑发布／订阅／槽名仍为 fbase_regress_pub／fbase_regress_sub／fbase_regress_slot。已通过 pgcluster 配置校验和三个实例目标匹配，并恢复 fbase-mac 登记及 mac 测试绑定。

本次只修改控制面登记／配置，未执行启动、创建、清理、SQL 或全套件回归。该次运行状态探测显示三个节点均未在线，恢复登记不表示数据库已经启动；旧测试产物也不会由环境登记自动重建。
