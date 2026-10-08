# fbasecman 开发环境接管记录

日期：2026-10-08（Asia/Shanghai）。环境 ID：`fbasecman-dev`，
目标主机：`192.168.1.24`，安装：`/usr/local/pgsql15.3-mmr`。
已有回归环境继续独立保存；本环境未绑定回归 profile，未执行公共测试准备。

## 当前实际拓扑（按开发配置对齐后）

| 节点 | 目录 | 端口 | 分组／角色 |
| --- | --- | --- | --- |
| pg_1 | /opt/data/mmr_rep4 | 15432 | pg_cluster_1 主库 |
| pg_3 | /opt/data/mmr_rep7 | 15433 | pg_1 的备库，application_name=pg_3 |
| pg_5 | /opt/data/mmr_rep9 | 15434 | pg_1 的第二台备库，application_name=pg_5 |
| pg_2 | /opt/data/mmr_rep5 | 25432 | pg_cluster_2 主库 |
| pg_4 | /opt/data/mmr_rep8 | 25433 | pg_2 的备库，application_name=pg_4 |

业务数据库为 postgres，与 new-config/fbasecman-all-new.conf 的 storage_db
一致。pg_1～pg_4 的名称、端口、cluster_name 与该配置一致；额外接管的
pg_5 属于 pg_cluster_1。该 fbasecman 配置文件仍引用四个端点，没有改写。

mmr_rep5 和旧 mmr_rep8 在升主后发生时间线分叉。按选定的数据保留方向，
保留 mmr_rep5，使用 pgcluster 的备库重建能力删除并重建 mmr_rep8。
第一轮基础备份因网卡地址缺少复制 HBA 条目失败，随后在目标主机用回环
连接重试成功；源主库 HBA 未放宽。其他数据目录未重建。

随后按开发配置对齐主备：核对 pg_1 与 pg_3 的系统标识、逻辑槽及其 WAL，
正常关闭旧主 pg_3，等待 pg_1 回放到关闭检查点后提升。原 mmr_rep7 和
mmr_rep9 分别保留为下列目录，再通过基础备份重建为 pg_1 的备库：

- /opt/data/mmr_rep7.before-platform-alignment-20261008
- /opt/data/mmr_rep9.before-platform-alignment-20261008

初次接管时 mmr_rep9 的系统标识独立；现在 pg_5 与 pg_1/pg_3 的标识均为
7493793257624617348。pg_2/pg_4 的标识为 7649323416152924352。

## 平台及部署引擎调整

- pgcluster 的 MMR 校验只要求 fdd_mmr；fbase_mac 和 fb_license 按声明选用。
- 离线角色识别只检查控制文件的 Database cluster state 字段，避免
  `Min recovery ending loc's timeline` 中的子串导致所有目录被判成备库。
- MMR 登记端点落在同一物理组的备库时，接管按系统标识关联实际主库，
  不改写数据库内的 MMR 成员元数据。
- 单个 MMR 环境支持 auxiliary_instances，把独立开发实例纳入实例清单、
  资源范围、状态及生命周期管理，不伪造 MMR 成员或物理复制边。
- Web 进程识别核对实际 argv，避免将包含 product-platform/start 字样的
  普通 shell 命令误认成 Web 服务。
- 同机物理复制使用回环地址；上游核对仅在主备部署于同一主机时接受
  回环连接，跨主机不能把 localhost 误认成远程主库。

## 接管与验收

采用现有 discover-existing → 草稿 → 不可变计划 → apply 流程。
首次计划 `fc85e5a08c474e63866fe8ed9ea0d00d` 的操作为 deployment.health，
任务 `034230a0-f613-42ca-8498-f02d0a925518` 为 SUCCEEDED，环境为 APPLIED。

在线核对五个端口对应的实际数据目录和角色；mmr_rep4、mmr_rep8 的
pg_stat_wal_receiver.status 均为 streaming。平台拓扑和状态 API 返回
五个节点及两条物理复制边；第五个实例没有复制边。页面验收核对新环境
及五节点画布，不对业务库写入测试数据。

对齐后的当前计划为 ad74d8e828374a2db37c8a0dbaa9dd92，健康任务
4dd621e8-6b04-4aec-a83b-19deab8a33da 为 SUCCEEDED，环境为 APPLIED，
目标为 mmr.mmr_group。原环境登记及不可变计划保留，没有删除执行历史。
三个备库均为 streaming，上游及 application_name 均在线核对匹配。
拓扑 API 核对 pg_1→pg_3、pg_1→pg_5、pg_2→pg_4 三条复制边及五个名称。
本次对齐后的浏览器检查因 Chromium 缓存缺失未执行；不沿用初次页面
验收作为新命名的浏览器验收。相关 pgcluster 配置/runtime/restore 测试
25 passed（7 subtests passed）。

代码验证：pgcluster 配置／runtime 测试 16 passed（7 subtests passed），
平台接管测试 16 passed，部署工作台／归属测试 44 passed；差异格式检查通过。

## 保留的边界

现有 fbasecman-all-new.conf 使用四个数据库端点，不使用 pg_5。
MMR node1 的登记 DSN 仍指向 15432；对齐后该端口已恢复为主库，原来的
角色不一致已消除。两端 MMR 订阅均收到 WAL；健康任务与物理复制验证仍
不等于 MMR 双向业务写入验收。没有修改 fbasecman 源码或该配置文件。
