# fbasecman Hint / sql_parse GUC 同步回归设计

日期：2026-10-08。已开始首批原生回归实现并执行定向真机检查；完整矩阵和产品内部验收尚未完成。设计覆盖、代码实现、已执行结果分别说明，不宣称完整通过。

依据：`/home/postgres/fly_dev/fbasecman_dev/docs/hint_guc_sync_alignment.md`，以及当前产品源码 `sources/frontend.c`、`sources/parser/fb_frontend.c`、`sources/deploy.c`、`sources/fb_guc_cache.c`。

本次对齐产品设计 §1、§3—§5.7、§6、§7.2 全部 16 项及 §8；设计文件 SHA-256：`ff2b84d152ac24d5bd7998120cdba5d72a9933f9b8fd087becc7fcd20ad2bc30`。指纹仅标识本次阅读版本，不代表已实现或执行。

## 源码核查

- Hint 的 `fb_frontend_handle_parse()` 调用 `fb_frontend_rw_change_possible_ex()`；普通 GUC 分类可经 `fb_need_change_to_master_classified()` 调用 `fb_handle_guc_cmd()`，后者直接应用正式缓存。后续 named statement 的 Bind/Describe 还会进入 `fb_frontend_route_prepared_stmt()`。必须分别检查 P、B、Describe Statement、Describe Portal，不只检查完整 PBDES。
- Hint Execute 当前通过 client 级 `bypass_state.skip_msg_type` 消费旁路响应；需要多 statement、多 portal 测试暴露候选串用。
- `fb_tx_guc_work_cache_enabled()` 当前仅对 sql_parse + enable_guc_sync 返回 true；Hint 普通事务 GUC 不能据此假定已进入工作缓存。
- sql_parse 的 Parse 保存 `guc_candidate`，Bind 深拷贝到 portal，Describe 只描述，Execute 本地成功路径应用正式缓存。事务 Execute 路径目前在转发前调用 `fb_sql_parse_record_execute_tx_guc()`，并非所有候选都在 CommandComplete 后才登记；因此失败路径必须独立测，不能把方案中的目标时序当成当前源码事实。
- `fb_tx_guc_handle_ready()` 结合 pending transaction command、真实 CommandComplete 与 RFQ 决定 promote/discard。失败事务执行 COMMIT 可能实际返回 ROLLBACK，必须核对真实标签。
- `fb_tx_guc_handle_parameter_status()` 允许事务内状态转发给客户端，但不直接写正式缓存。因此“事务内不能收到 ParameterStatus”是错误期望。
- `fb_tx_guc_savepoint_command_complete()` 维护保存点 marker/undo；`od_deploy()` 有工作缓存时优先部署工作缓存，否则部署正式缓存。
- 产品设计 §3 明确区分现有 sql_parse 顺序与 Hint 新约束：sql_parse 普通事务 E 先预记录再登记 outstanding，本地 GUC 先构造响应、detach、apply，再入队；不因 Hint 目标顺序不同判 sql_parse 回归失败。Hint 现有部分多语句 Q 使用 sql_parse 的 simple_guc_batch 入口，本次与新增场景统一接入公共 owner/上下文接口，保留隐式事务成功边界；同类型存储可经接口共用，确有生命周期冲突时才分实例。
- Hint 的 `SET READ ONLY/WRITE` 是代理路由标签，不能等同于 sql_parse 的事务属性 SQL；Hint 显式事务中不允许按标签切换后端。

## 覆盖矩阵

保留以下 18 个场景的全部测试内容，合并为 4 个综合场景，Hint 和 sql_parse 分别注册，共 8 条新增用例。每条用例内部都执行 MMR 和主备复制组两个拓扑阶段，不再按拓扑增加注册目标。事务类场景内分别使用 Simple Query 单语句请求和 Extended Query 请求执行，各使用独立客户端。按更新后的产品设计，增加显式/隐式事务段的多语句单 Q、后端安全回收及定点故障注入子检查，不增加注册入口。

目标命名：`guc.<综合场景>_<hint|sql_parse>`。hint 与 sql_parse 对已支持的业务场景使用同一 GUC 期望；Hint 事务 E 和单语句 Q 预记录顺序复用 sql_parse 基线，本地提交复用现有顺序及失败终止原则，sql_parse 按原有基线执行；后端路由通过各自模式合法的操作控制，不强求相同路由语法或完全相同的响应字节流。

| 综合场景（每行分别注册 hint/sql_parse） | 完整子场景映射 |
| --- | --- |
| `extended_boundary` | extended_parse_no_execute、extended_bind_describe_no_execute、extended_execute_apply、extended_statement_isolation、extended_portal_isolation、extended_candidate_cleanup、local_commit_failure、compatibility_scope |
| `transaction_sync` | tx_set_commit、tx_set_rollback、tx_reset_commit_rollback、tx_reset_all_commit_rollback、tx_error_abort、tx_batch_segments、set_local_scope、execute_registration_failure |
| `savepoint_report` | savepoint_rollback、savepoint_nested_release、report_parameter_status |
| `backend_redeploy` | tx_disconnect_cleanup、session_backend_redeploy、routing_and_discard_boundaries、mode_owner_isolation、local_backend_reclaim |

合并只减少目录里的用例入口，不减少协议阶段、拓扑、SQL 操作、成功/失败分支或断言。下面每个子场景均保留独立的步骤结果与证据。

## 配置范围与基线限制

- Extended 对齐：enable_guc_sync=yes 且 pool_reserve_prepared_statement=yes，测试 statement/portal 候选、事务跟踪及本地提交。开关 no 的 Extended 单独检查原透传行为、不建立本地候选，不套用本方案事务缓存承诺，不计入 E 对齐覆盖。
- Simple Q：预处理保留开关 yes/no 均执行，并展开 transaction/session pool。提交后换后端检查在 transaction pool 用路由/交接控制；session pool 通过模式合法路由或独立安全回收后验证，不强求原物理后端交给另一客户端。
- enable_guc_sync=no 保持原行为，保留改造前后同输入的兼容基线检查；不计为启用同步后的对齐验收。
- 复杂场景以当前 sql_parse 代码与实际基线为范围依据。隐式 Q 段遇到 BEGIN（如 `SET work_mem='32MB'; BEGIN; ...`）的候选迁移/保存点恢复、同一 Sync 周期流水线 SAVEPOINT/SET/ROLLBACK TO/后续 SET，先登记限制，保留基线行为；不按 PostgreSQL 原生支持为 Hint 新增算法。
- 保存点正常及错误恢复全部分步确认：Q 每条请求读取至 RFQ；E 每条命令采用独立 P/B/E/Sync 周期并读取成功/错误及 RFQ，确认后才发后续命令。结果不泛化为同周期保存点流水线支持。
- 有代码依据的隐式段实际 COMMIT/ROLLBACK 结算不与“隐式段遇到 BEGIN”混为一类。先验证 sql_parse 单 Q 基线，基线通过后 Hint 对齐；未通过或未确认时登记限制，不为 Hint 单独修复，也不将未执行显示 PASS。

## 最终执行矩阵

以下矩阵统一承接产品文档 §4.2、§4.3、§5.1—§5.7 和 §7.2 的要求，实施时以本节为完整计划，不再依赖追加说明。业务协议行在 Hint/sql_parse × MMR/主备执行；产品侧故障注入行按两种模式各自的调用边界执行并登记关联拓扑集成结果，不伪造为数据库 SQL 步骤。协议栏中的 Q 指单语句 Simple Query，E 指 Extended Query，多语句单 Q 另行明确。每个协议/配置变体使用独立客户端。Q 行覆盖 pool_reserve_prepared_statement=yes/no × transaction/session pool；E 对齐行限定 enable_guc_sync=yes、pool_reserve_prepared_statement=yes，池模式按场景展开。两种模式分别判定，不要求路由语法或整个回包序列相同；sql_parse 修改前后使用相同输入，核对结果及响应顺序不变。Hint 事务 E 和单语句 Q 均沿用 sql_parse 已有预记录顺序；本地提交两者复用既有顺序及失败结果，不新增原子交换框架。

| 综合场景 / 子场景 | 协议与执行分支 | 操作与核心断言 |
| --- | --- | --- |
| extended_boundary / extended_parse_no_execute | E；事务外本地路径、事务内后端路径 | 建立 work_mem=8MB 基线；仅 P 一个 SET 32MB + Sync，查询仍为 8MB。RESET、RESET ALL 同样未 E 不得改变值，无执行 CommandComplete。事务内先 BEGIN 并真实查询确立后端，P 后正式缓存及工作缓存均不变。补未 E 断连后复用原物理后端检查；该检查不能替代原客户端旧值断言。 |
| extended_boundary / extended_bind_describe_no_execute | E；两条路径；P+B、P+Describe Statement、P+B+Describe Portal 分别执行 | SET、RESET、RESET ALL 均未 E 不生效；记录 ParseComplete、BindComplete、描述响应及最终 RFQ，无执行标签。继续执行同一 portal 时遵守下一节的 Flush 顺序。两条响应来源通过日志关联或产品侧测试确认，不能凭 ParseComplete 字节判断。 |
| extended_boundary / extended_execute_apply | E；SET、RESET、RESET ALL；两条路径 | P/B/D 后观察旧值，E+Sync 安全收口后观察新值；仅在基线支持且安全同步条件满足时检查同周期新值。SET work_mem=32MB、statement_timeout=7s，逐项精确核对。含本地 SET/RESET 在真实后端 SELECT 前、后的混合周期，以及全部本地操作周期；逐请求核对响应归属和顺序，每周期仅一个 RFQ。SELECT 不得命中 heartbeat。 |
| extended_boundary / extended_statement_isolation | E；全局缓存首次解析及命中 | 登记 A=SET 32MB、B=SET 64MB，按 B、A 执行，在安全收口后的查询值依次为 64MB、32MB，同周期观察受安全同步条件约束；未执行 RESET 不得覆盖候选。交错不同参数名和值，覆盖未命名 statement 的合法替换、已登记 statement 重复执行；逐次核对命令标签归属，并覆盖本地 portal 的重复 Execute 和 Close 后同名重建，旧候选不能复活。 |
| extended_boundary / extended_portal_isolation | E；同周期内多 portal | 同一 statement 绑定 p1/p2，关闭 p1 后执行 p2；交错另一 statement 的 portal，核对对应候选。已绑定 portal 的候选不受后续请求覆盖；替换 statement 时使用协议允许的顺序，不跨事务结束假定 portal 存活。 |
| extended_boundary / extended_candidate_cleanup | E；Close、断连、协议错误 | 关闭未执行 statement/portal 后参数不变，新候选执行不能串入旧值。断连后检查原池后端及新客户端。未知 statement/portal 分别声明目标版本的 SQLSTATE。另在同周期先 E 执行 SELECT 1/0，再发送 SET 的 P/B/D/E，最后 Sync：22012，错误后的请求无成功响应、无参数副作用；Sync 后独立查询恢复原值。 |
| transaction_sync / tx_set_commit | Q、E、多语句单 Q | 8MB → BEGIN → SET 32MB → 事务内查询 32MB → COMMIT；标签 COMMIT、最终 RFQ I；同一客户端换后端后仍为 32MB。补启动时冻结 application_name 基线，单 Q `BEGIN; SET application_name='hint_tx'; COMMIT;`，提交后查询及换后端仍为 hint_tx；sql_parse 使用自己的模式标识。 |
| transaction_sync / tx_set_rollback | Q、E、多语句单 Q | 8MB → BEGIN → SET 32MB → ROLLBACK；事务内为 32MB，结束及换后端后为 8MB。application_name 对应 ROLLBACK 分支恢复启动基线。 |
| transaction_sync / tx_reset_commit_rollback | Q、E；COMMIT/ROLLBACK 独立分支 | 基线 8MB，事务 RESET 后读取冻结的 RESET 目标值；COMMIT 后及重部署后保持该值；独立事务 RESET 后 ROLLBACK 恢复 8MB。 |
| transaction_sync / tx_reset_all_commit_rollback | Q、E；COMMIT/ROLLBACK 独立分支 | 同时设置 work_mem、statement_timeout；事务 RESET ALL 后逐项核对 RESET 目标值；COMMIT 后保持，ROLLBACK 后恢复两个会话值，覆盖删除多个缓存键。 |
| transaction_sync / tx_error_abort | Q、E；完整回滚及失败事务 COMMIT | SET 32MB 后 SELECT 1/0，核对 22012、RFQ E；后续普通 SET 为 25P02。分别 ROLLBACK 或失败事务 COMMIT（真实标签 ROLLBACK），结束及换后端后为 8MB。另测事务内 E 执行无效 SET work_mem='not_a_memory_size'，核对目标后端 SQLSTATE（PostgreSQL 基线 22023），失败候选不得提交。 |
| transaction_sync / tx_batch_segments | 多语句单 Q；补充子检查，不新增注册入口 | 单 Q `BEGIN; SET work_mem='32MB'; COMMIT; SELECT current_setting('work_mem');` 为 32MB；ROLLBACK 对应分支为 8MB。单 Q `SET work_mem='32MB'; SELECT 1/0;` 隐式事务失败后独立查询恢复 8MB。单 Q `BEGIN; SET work_mem='32MB'; COMMIT; BEGIN; SET work_mem='64MB'; ROLLBACK; SELECT current_setting('work_mem');` 的末尾查询已经为 32MB；另测前段提交、后段错误，完整回滚后前段值仍存在。另测隐式段成功 `SET work_mem='32MB'; SELECT 1;`，最终 RFQ(I) 后正式缓存为 32MB；混合段 `BEGIN; SET work_mem='32MB'; COMMIT; SET work_mem='64MB'; SELECT 1/0;`，后续独立查询及重部署保留 32MB。所有隐式成功/失败及混合分支同步覆盖 report GUC（TimeZone/application_name），核对缓存与客户端状态；失败事务 COMMIT 必须按实际 ROLLBACK 标签处理。逐语句关联 CommandComplete，当前显式或隐式段在实际 COMMIT/ROLLBACK 标签处独立结算并标记已结束；未被显式结束的隐式段等无错误且 RFQ(I) 后提交，RFQ(T) 不提升。补单 Q `SET work_mem='32MB'; COMMIT; SELECT 1/0;`，先 sql_parse 基线确认，再 Hint 核对最后正式值及重部署为 32MB；对应 ROLLBACK 恢复原值。增加实际结束后新段 SET 成功/失败，核对旧候选不重复应用；同步覆盖 report GUC。不为中间边界虚构 RFQ。 |
| transaction_sync / set_local_scope | Q、E；每条 SQL 独立请求 | 8MB → BEGIN → SET SESSION 16MB → SET LOCAL 32MB；事务内 32MB，COMMIT 后及重部署后为 16MB；ROLLBACK 分支恢复 8MB。SET LOCAL 真实下沉，不进入提交后的会话缓存。 |
| savepoint_report / savepoint_rollback | Q、E；分步独立周期确认；SET/RESET/RESET ALL；成功及错误恢复 | 8MB → BEGIN → SET 16MB → SAVEPOINT → SET 32MB → ROLLBACK TO → 16MB → COMMIT；重部署后仍为 16MB。补 RESET/RESET ALL 撤销。另在保存点后成功 SET 32MB，再执行无效 SET，ROLLBACK TO 后恢复 16MB；RFQ(E) 时保留可恢复的工作缓存、marker、Undo；ROLLBACK TO 成功恢复后可继续查询/SET 再提交。错误响应本身不代表整事务回滚；失败不能清掉保存点之前的成功值。 |
| savepoint_report / savepoint_nested_release | Q、E；分步独立周期确认；嵌套、重复名称、带引号名称 | 设置 16/32/64MB；RELEASE 不恢复参数，RELEASE 内层后 ROLLBACK TO 外层恢复目标 marker 之前的值，仍在原事务。核对最终提交及重部署结果。 |
| savepoint_report / report_parameter_status | Q、E；分步独立周期确认；COMMIT、ROLLBACK、事务错误、ROLLBACK TO | TimeZone 基线 UTC，事务改 Asia/Shanghai；采集真实 ParameterStatus 和 SQL 值，核对各分支最终客户端状态及换后端结果。允许事务内状态转发，不允许其提前写正式缓存。补 UTC → BEGIN → SET Asia/Shanghai → SAVEPOINT → SET Asia/Tokyo → 错误 → ROLLBACK TO → COMMIT，Q/E 都核对恢复及提交为 Asia/Shanghai，不能被正式基线 UTC 覆盖。保存点错误阶段不强制从正式缓存补发；恢复后沿用正确的原生 ParameterStatus，仅按既有规则必要时补发。 |
| backend_redeploy / tx_disconnect_cleanup | Q、E；未提交断连 | BEGIN、SET 32MB 后断连，按后端复用步骤让新客户端复用原物理连接，读到新客户端冻结基线，无未提交参数残留。 |
| backend_redeploy / session_backend_redeploy | Q、E；客户端 A/B/C | A 设置 32MB 并释放后端；B 复用原连接设置 64MB；A 再复用后仍为 32MB，B 仍为 64MB，新 C 为其启动基线。内部 deploy 的 CommandComplete 不得算作业务 SET 响应；内部 ParameterStatus 不得将客户端状态改成旧后端值。 |
| backend_redeploy / routing_and_discard_boundaries | Q、E；读写切换、DISCARD ALL | GUC 前后进行模式合法的路由操作并核对身份、参数；Hint 标签不进入普通 GUC 候选。事务内 DISCARD ALL 由后端拒绝（25001），完整回滚后保留会话值；事务外成功 DISCARD ALL 后才清缓存，核对默认值及连接可用。 |
| backend_redeploy / mode_owner_isolation | Q、E；补充子检查，不新增注册入口 | 同一个代理配置两个分别声明 Hint/sql_parse 的用户，交错请求；Hint 失败/清理后，sql_parse 未完成事务仍按自己的声明完成。两条模式用例均保留主断言。结合产品侧验收核对 owner 独立或明确适配契约，两个独立代理不能证明同进程隔离。 |
| extended_boundary / local_commit_failure | 产品侧定点故障注入；公共本地提交路径 | 完整响应构造、apply/cache 更新、apply 成功后的响应入队分别失败；本条无 SET/RESET 成功响应入队/发出，不误清此前已确认响应，沿用既有失效及连接终止路径。apply 后入队失败允许关闭中的客户端缓存已改变，不要求恢复后继续服务；状态不可信后端不得直接回池。分别留证两种模式失败结果。 |
| transaction_sync / execute_registration_failure | 产品侧定点故障注入；E、事务内单语句 Q | E 资格通过后先预记录，再准备 pending、登记/转发；Q 路由/attach 后预记录再转发。分别注入预记录、后续 pending/登记/转发失败，验证无本条错误成功响应、无正式提升，公共失败返回和 owner 清理结果不变；状态不可信后端关闭或安全 reset，不能强求 outstanding 失败时工作缓存从未变化。 |
| backend_redeploy / local_backend_reclaim | E；未挂载 / 已挂载 transaction pool / session pool；安全及受限分支 | 未挂载后端时执行本地 SET/RESET，再 attach 查新值；已挂载 transaction pool 后端满足条件后 reset/detach，再 deploy 新值。session pool 或 outstanding/物理 portal 存活时登记待回收、标记旧后端 offline，安全 Sync/RFQ 后回收；不能提前破坏 portal、伪改 server cache 或在未同步旧后端查新值。P/B/D 不置待回收标记。受限同周期场景按 sql_parse 已支持边界单独判定，不透明迁移物理 portal。 |
| extended_boundary / compatibility_scope | Q/E；sync=no，Extended reserve=no | 关闭同步时执行事务内 SET/查询/ROLLBACK，核对既有透传语义；reserve=no 的 Extended 仅 P 不生效、E 真实执行，不要求事务缓存跟踪。独立记录配置与实际报文，不计作启用同步后的对齐能力。 |

保留全部原有 18 个子场景；tx_batch_segments、mode_owner_isolation、local_commit_failure、execute_registration_failure、local_backend_reclaim 是综合场景内部的稳定检查标识，共 24 行，不增加 8 条注册目标。覆盖计划还需展开每行的协议、执行分支和拓扑，不以表格行数代替实际检查数。sql_parse 已知队列槽位覆盖、SET LOCAL 批次限制单独登记，不能跳过失败或放宽 Hint 期望。

## 分阶段协议执行顺序

以下顺序固定使用同一客户端；`D(S)` 为 Describe Statement，`D(P)` 为 Describe Portal，`H` 为 Flush，`S` 为 Sync。观测 SELECT 使用独立命名 statement/portal，读取参数和后端身份，避免覆盖未命名对象。Flush 后按预先声明的响应类型和数量有界读取，不能调用等待 RFQ 的接口，也不以固定 sleep 作为处理完成依据。

| 检查 | 报文顺序与读取边界 |
| --- | --- |
| 仅 P，之后执行同一 statement | `P(guc_stmt) → S → 读取至 RFQ → 独立查询旧值`；随后重新 `B(guc_portal, guc_stmt) → E(guc_portal) → S`，查询新值。跨 Sync 只保留合法的 named statement，不保留旧 portal。 |
| P+B / P+D(S) / P+B+D(P)，不再执行该 portal | 各独立发送对应报文后 `S → 读取至 RFQ → 独立查询旧值`；检查没有 SET/RESET 执行标签。事务外 portal 随事务结束销毁，后续执行必须重新 Bind。 |
| 分阶段观察后继续 E 同一 portal（事务内或安全支持的本地周期） | `P(guc_stmt) → H → 读取 ParseComplete`；`B(guc_portal) → H → 读取 BindComplete`；`D(P) → H → 读取描述响应`。每阶段后用另一命名 SELECT 的 `P/B/D/E/H` 查询旧值，读取该查询的完整响应但不等待 RFQ。最后 `E(guc_portal) → H → 读取执行响应 → S → 读取唯一 RFQ → 独立查询新值`。只有安全同步条件满足且 sql_parse 基线已支持时，才在 E 与 S 间另加 Extended SELECT 检查新值。整个 P 到 E 过程不插入 Simple Q 或中间 Sync；事务外本地周期若观测 SELECT 引入未结束后端事务或物理 portal，则采用上一行的独立未 E 周期加重新 Bind 执行，不强制该混合周期返回新值。内部快照仍必须覆盖 P/B/D 各边界。 |
| 事务内后端路径 | 先以独立 BEGIN 和真实查询确认 RFQ T 及绑定身份，再执行上述分阶段顺序；最终 Sync 为 T。COMMIT/ROLLBACK 用后续独立请求完成，不在 GUC 的 P 与 E 之间主动改变事务状态。 |
| 多 statement/portal、混合响应及错误后忽略 | 所有待验证 portal 保持在同一合法周期。按矩阵排列请求，最后一次 Sync 收口；保存已收响应、忽略的请求及唯一 RFQ。出错后不等待被忽略请求的成功响应。 |

SET/RESET/RESET ALL 的 Describe 应核对对应的合法 ParameterDescription/NoData 等响应；SELECT 观测请求应有自己的 RowDescription、DataRow、CommandComplete。逐请求关联，不能把观测 SELECT 的执行标签误算成 GUC 已执行。分阶段用例在基线支持的安全条件下覆盖本地 GUC 与后端 SELECT 混合周期；保留本地在前、后端在前和全本地周期的分支。

Flush 不产生额外客户端 RFQ，不提交未 E 候选。混合本地/后端组合先执行 sql_parse 基线，记录响应顺序、错误、事务状态和可见 RFQ；已验证支持的组合才要求 Hint 等价，未支持/未确认者列限制，不新增 Hint 同步屏障或等待策略。公共路径原有内部 Sync 与响应吸收保持原条件，不能将“内部绝无 Sync”作为验收要求。存在物理 portal、事务或 outstanding 时，后续请求按基线已支持限制处理，不透明迁移 portal。不能在本地 E 内阻塞等待尚未读取的客户端 Sync。

## 缓存写入与架构验收（必需）

平台黑盒回归证明协议行为、参数值、会话隔离及重部署；产品侧可控处理器测试证明内部缓存写入边界和请求归属。两类验收均必需，源码 review 补充调用顺序和所有权检查，不能以 SQL PASS 或 review 代替内部边界测试。每个产品侧测试保存执行阶段、候选 owner、正式缓存/工作缓存前后快照及断言，按参数键和值比较，不依赖日志时间推测。

| 边界 | 产品侧必须核对 |
| --- | --- |
| P/B/D | 两条路径的正式 client cache 和事务工作 cache 均不改变；允许保存解析元数据、建立符合资格的本地候选及 Bind 深拷贝。事务内不得建立普通本地旁路候选。 |
| 事务外 E 本地成功 | 对基线支持的组合，Hint 复用前序错误处理和响应排序，通过抽取的公共函数执行完整响应构造 → 安全 detach/待回收登记 → fb_apply_guc_cmd() → 成功响应入队。P/B/D、未知/关闭 portal、错误后被跳过的 E 不调用 apply。apply 失败不入队本条成功响应；apply 成功但入队失败也终止连接，复用既有失效/清理路径，不要求回滚关闭中客户端缓存或继续服务，不创建暂存/原子交换框架。sql_parse 条件、顺序和失败结果不变。 |
| 事务内 Extended E | 执行资格检查通过后预记录工作缓存，再准备 pending 归属、调用通用 Execute 登记 outstanding 并转发，沿用 sql_parse 顺序。bind_failed、本地旁路及失败事务等跳过路径不得预记录。预记录或后续准备/登记/转发失败复用公共返回、pending 队尾撤销和上层错误清理，不提升正式缓存。 |
| 事务内单语句 Simple Q | 路由和 attach 成功、确认真实转发后预记录工作缓存，再转发原 Q；CommandComplete 仅确认完成，不重复记录。不新增等待回包的候选/回调。预记录与转发失败按公共失败路径清理，不提升正式缓存。 |
| 多语句 Simple Q | 冻结批次元数据，在对应 CommandComplete 后记录/确认；当前显式或隐式段按实际 COMMIT/ROLLBACK 独立结算。未显式结束的隐式段在无错误且 RFQ(I) 后提交；RFQ(T) 不提升。旧 Hint 入口与新增场景统一接入公共 owner 批次接口，不提前应用未执行后缀。 |
| 提交、回滚及保存点 | Q 当前显式或隐式段按实际 COMMIT 标签独立提交，实际 ROLLBACK 丢弃并标记已结算；E 事务结束在实际 COMMIT 已确认且 RFQ 成功后提升，同步 server cache。ROLLBACK/失败事务 COMMIT 不提升。ErrorResponse 定位失败/跳过请求；有可恢复保存点的 RFQ(E) 保留工作缓存、marker、Undo，无保存点 RFQ(E)/完整回滚/断连才丢弃。ROLLBACK TO 恢复事务视图，RELEASE 不恢复值，失败候选不得提交。 |
| SET LOCAL 与 report 参数 | SET LOCAL 不进入提交后的会话缓存；事务内 ParameterStatus 不绕过工作缓存修改正式缓存；内部 deploy 响应不作为业务完成边界。完整回滚或隐式段失败按最后已提交值修正客户端状态；保存点错误不一律补正式基线，ROLLBACK TO 后以恢复的事务视图为准。 |
| 候选和模式归属 | statement/portal 候选深拷贝及引用管理共用，替换或清理不影响其他 owner。pending_forbidden、pending_tx_command、simple_guc_batch 的类型、操作及成功/错误代码优先复用，存储可在公共 owner 接口下共用；生命周期确有冲突时使用同一实现的不同实例。保存点优先共用 pending 类型与 marker/Undo 算法，确需其他记录类型才抽取最小原语。禁止绕过 owner 套用 sql_parse 路由语义。 |

上述 Hint 事务 E、单语句 Q 和多语句 Q 分别沿用 sql_parse 已有顺序验收，本地提交按公共现有顺序及失败终止原则验收；sql_parse 按改造前的原有调用顺序、结果和响应顺序验证不变，不把 Hint 新要求套入 sql_parse 或为此修改其路径。两种模式的共有业务期望不放宽；sql_parse 原有问题独立登记。定点故障注入属于本轮必需产品侧验收，注入点与断言见下一节。

### 公共实现复用 review 条件

源码 review 必须为下列能力记录公共函数/类型、Hint 适配入口、sql_parse 原调用入口以及条件/顺序是否保持：候选深拷贝和 statement/portal 引用管理；本地 P/B/D 响应、本地 E 提交与安全 detach；Q 批次准备、逐条确认、段结算与 RFQ 清理；事务缓存创建/记录/提升/丢弃、保存点及 ParameterStatus；前序响应发送/读取/等待循环。

能直接调用的函数直接调用，仅在模式门控、事务状态、路由状态或响应归属不同处增加 owner/上下文参数或薄包装。不为 Hint 复制整套生命周期、队列、Undo 或本地提交框架；不直接调用执行 sql_parse 路由决策的整个前端入口。旧 Hint 批次入口须统一经过 owner 接口，抽取前后 sql_parse 的条件、调用顺序、错误返回和清理结果分别核对。

混合响应公共路径保留 sql_parse 已验证的发送、等待、内部 Sync、响应吸收及错误清理条件，不强制内部 Flush 或新执行屏障。owner 隔离产品侧测试构造待处理记录并验证确认、错误和清理只操作匹配 owner；跨客户端交错黑盒结果不能单独证明共享接口正确。

## 定点故障注入与旧后端安全回收

故障注入仅作用于测试自有代理/产品侧测试进程，用可控返回值或定点 failpoint 模拟对应资源失败，包含该路径的分配失败；不实施系统内存耗尽或无关随机故障。每个点单独运行，先建立已确认值 V1 和待执行值 V2，保存本条请求、响应队列、正式/工作缓存、outstanding、后端在线/待回收状态及资源清理事实。快照用于确认实际失效和清理；apply 或入队失败导致关闭时，不能强求客户端缓存仍为 V1。注入点未实现时记录 BLOCKED/未执行，不能以普通 SQL 错误代替，也不能宣布完整验收。

| 注入点 | Hint 目标断言 | sql_parse 对照 |
| --- | --- | --- |
| 本地完整响应构造 | 构造失败不调用 apply、不入队本条成功响应；释放本条资源，不误清已确认响应，沿用既有失败/终止结果。 | 公共构造实现两种模式分别留证，原条件及失败结果不变。 |
| fb_apply_guc_cmd/cache 更新失败 | 本条成功响应未入队/发出，复用既有缓存失效和连接终止处理；不以缓存仍等于 V1 为 PASS 前提，不在未知状态继续服务。状态不可信后端关闭或安全 reset 后才处理。 | 公共 apply 实现保持原条件、失效及失败终止结果。 |
| apply 成功后响应入队失败 | 顺序证据为 apply 已成功、入队失败；不发送本条成功响应，终止客户端，不要求恢复关闭中缓存。不误清此前已确认响应，不允许不可信后端直接回池。 | 公共入队失败路径两种模式分别验证；不新增响应/缓存原子交换或失败后继续服务机制。 |
| E 或单语句 Q 预记录失败 | E 不继续准备/登记/发送；Q 不转发。沿用内部失败返回及公共清理，正式缓存不提升。 | 使用相同公共记录失败点核对原顺序及结果。 |
| E 预记录后的 pending 准备/outstanding 登记/转发失败；Q 预记录后转发失败 | 不误认执行成功，不提升正式缓存；核对当前请求 pending 队尾撤销、事务结束状态清理及上层错误/断连结果。工作缓存可能已有候选，不要求另建恢复框架；不可信后端不能直接回池。 | 同阶段公共失败返回及清理顺序不变，按 owner 分派。 |
| 保存点 marker/Undo 跟踪或恢复失败 | 无伪造成功或遗漏 Undo 的提升；已有后续命令下沉则关闭，否则进入失败事务且只允许完整回滚，清理只作用于 Hint owner。 | 按既有失败原则验证改造前后结果不变。 |

混合请求及一次发送无客户端 Flush 的分支先记录 sql_parse 的成功/前序错误基线，小响应避免缓冲区自动刷新掩盖问题。仅对已支持组合要求 Hint 结果、响应顺序及每个客户端 Sync 的一个可见 RFQ 等价；未支持组合登记限制。保留公共路径内部发送/同步事实辅助定位，不要求新增内部 Flush，不禁止原有内部 Sync；客户端 RFQ 数量不用于猜测内部同步次数。

旧后端回收必须按下表检查；客户端可见值与内部 server cache 分开取证，不能只改 server cache 为 V2 来伪造物理同步。

| 旧后端状态 | 执行及验收 |
| --- | --- |
| 未挂载 | 本地 E 成功后首次真实 SELECT 经 attach/deploy 得到新值；无未执行候选部署。 |
| 已挂载 transaction pool、满足安全条件 | 先以真实查询/产品侧可控状态证明挂载，再本地 E；无事务/COPY/outstanding/物理绑定 portal 且池允许时 reset/detach，后续查询经 deploy 得到新值。普通 SQL 未保持挂载时不得冒充此分支通过。 |
| session pool | 独立代理或用户配置 `pool "session"`；本地 E 后登记待回收并标旧后端 offline，在安全 Sync/RFQ 回收，之后同一客户端查询新值。不能直接套用 A/B/C transaction pool 交接步骤。 |
| outstanding、事务或物理 portal 仍存活 | 产品侧可控响应加合法混合请求验证不提前 reset/detach，不透明迁移物理 portal；同步条件不足的同周期后续请求按 sql_parse 已支持限制处理。安全边界后查询新值；本地 P/B/D 不设置待回收状态。COPY/事务不能错误进入本地候选路径，内部守卫测试不等同于新增 COPY 业务套件。 |

## 产品设计 §7.2 覆盖追踪

| 产品测试项 | 本方案对应检查 |
| --- | --- |
| 1：同连接未 E、断连复用 | extended_parse_no_execute、extended_bind_describe_no_execute、extended_candidate_cleanup |
| 2：E 生效及三种挂载/池状态 | extended_execute_apply、local_backend_reclaim |
| 3：多 portal | extended_portal_isolation |
| 4 / 5：事务提交 / 回滚 | tx_set_commit、tx_set_rollback |
| 6：保存点提交 | savepoint_rollback、savepoint_nested_release |
| 7：report 正式缓存隔离 | report_parameter_status、产品侧缓存边界 |
| 8：单 Q application_name 事务 | tx_set_commit、tx_set_rollback |
| 9：隐式 Q 成功/失败、report/non-report | tx_batch_segments |
| 10：混合事务段及真实结束标签 | tx_batch_segments、tx_error_abort |
| 11：Q/E 保存点错误及 report 恢复 | savepoint_rollback、report_parameter_status |
| 12：混合周期顺序、错误门控、唯一 RFQ | extended_execute_apply、extended_candidate_cleanup、公共前序错误基线检查 |
| 13：Close、Flush、重复 E、同名重建 | extended_statement_isolation、extended_portal_isolation、extended_candidate_cleanup、分阶段协议顺序 |
| 14：故障注入及 owner 隔离 | local_commit_failure、execute_registration_failure、mode_owner_isolation、定点故障注入表 |
| 15：隐式段实际 COMMIT/ROLLBACK 结算 | tx_batch_segments、缓存边界、配置范围与基线限制 |
| 16：无客户端 Flush 的混合响应基线 | extended_execute_apply、extended_candidate_cleanup、公共混合响应基线检查 |

## 默认值与 RESET 基线

每个拓扑/模式先以相同代理用户、storage_user、数据库、启动参数建立独立干净客户端，冻结参数启动值 S0；再在独立基线客户端实际执行 RESET/RESET ALL，冻结各参数的目标值 R0。基线记录启动 application_name、其他启动参数、数据库/角色默认配置、实际后端身份和参数值，不以当前被测操作结果反推期望。

- work_mem 按字节、statement_timeout 按毫秒比较；不硬编码数据库默认值。8MB/16MB/32MB/64MB 是测试主动设置值。
- application_name 使用显式 startup 值，例如 hint_base/sql_parse_base；ROLLBACK 期望为事务前值，RESET 期望按独立采集的 R0 判定，不能默认空字符串。新客户端 C 使用自己的启动基线。
- TimeZone=UTC 是场景主动设置的会话基线；RESET 的 R0 另行采集，不将 UTC 自动认作数据库默认值。
- 独立核对所有允许切入的物理后端的数据库/角色默认配置及干净连接值，代理基线与物理基线分别留证。若默认值不同，先明确产品部署的 RESET 语义与目标期望；未明确前该分支 BLOCKED，不据其中一个后端的值判断另一个后端通过。
- RESET/RESET ALL 的提交、回滚、保存点撤销及重部署都使用预先冻结的 S0/R0。跨模式使用相同业务期望，但 application_name 等模式标识保留各自声明值。

## 后端切换与复用的执行步骤

后端切换和 A/B/C 复用分支在每个拓扑阶段启动测试自有代理，使用 `pool "transaction"`、`pool_size 1`、`pool_discard no`、`pool_reserve_prepared_statement yes`、`enable_guc_sync yes`；限定测试用户、数据库和目标路由，避免外部业务竞争。pool_size 的约束按实际后端池分别检查，不假定全组只有一个后端。连接建立、等待空闲和目标获取统一使用可配置的有界轮询，默认总时限 30 秒，保存每次实际身份；前提不足或始终未获取目标记 BLOCKED，已观察到错误参数则 FAIL。local_backend_reclaim 另按上一节建立 session pool 和已挂载/未挂载分支；不能以默认 transaction pool 代替 session pool 覆盖。

1. **冻结路由候选。**读取 SHOW GROUP_ROUTING 和成员状态，记录写目标及可读目标；MMR 需两个可用主节点，主备需健康 primary/standby。复制组先补齐配置授权、成员配置与就绪检查，不只修改连接 database。身份统一为地址、端口、PID，另保存 recovery 状态。
2. **同一客户端换后端。**A 通过模式合法的写侧路由查询取得 W 并设置基线，完成被测事务。Hint 在事务外使用路由标签的完整 SQL `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY/WRITE`；文中 SET READ ONLY/WRITE 是简称，不能按该简称发送报文。sql_parse 使用声明 READ ONLY/READ WRITE 的合法事务，事务内 SELECT 同时读参数和身份。将 A 引导到读侧 R，再回写侧 W，逐次断言身份、路由角色和参数。Hint 不在显式事务中切后端；sql_parse 不为本轮额外构造事务内读转写。参数和身份必须由同一查询取得。
3. **MMR 无法由角色自然换到另一节点时。**只在测试自有代理中，将另一健康 MMR 节点设为 write target（使用已支持的 SET NODE WRITE ... IN GROUP mmr_group），等待路由快照确认，再由事务已结束的 A 进行写侧查询；核对新节点身份及参数，finally 恢复原目标并核对成功。不对主备强制晋升，不停止开发数据库。切换依据和前后快照全部归档。
4. **A/B/C 复用同一连接。**固定写侧候选及路由，A 查询身份 K、设置 32MB 后以 RFQ I 释放；B BEGIN 后先查询身份，必须等于 K，再 SET 64MB、COMMIT；A 再 BEGIN 查询身份与值，必须为 K、32MB，COMMIT；B 再查必须为 K、64MB；新 C 查 K 及自己的启动基线。每次事务结束后再交接连接，失败时先回滚清理，不让占用者和等待者相互阻塞。
5. **原连接被其他池连接抢占时。**仅在配置允许多个连接的独立分支中，辅助客户端在各非目标连接 BEGIN、查询身份后保持事务，占用非目标连接；目标 K 保持空闲，再让被测客户端获取 K。辅助客户端不得占住目标或在唯一目标 pool_size=1 时阻塞被测客户端；测试结束全部 ROLLBACK/关闭。不能以占用连接保证节点路由，节点选择仍以路由快照和业务身份为准。
6. **未 E/未提交断连复用。**P/B/D 或事务内 SET 后，先用观测查询记录目标 K，再断开原客户端；等待回收后新客户端在固定目标路由取得 K 并检查基线。如果断连导致 K 被销毁而只取得新 PID，则保留清理事实，但原物理连接复用检查为 BLOCKED，不能算 PASS。

## 观测与执行约束

1. 同一客户端使用长连接裸协议探针，逐阶段保存发送和接收报文。未执行后结束周期的检查用 Sync 收口；继续 E 同一 portal 的检查遵循分阶段协议顺序，使用 Flush 与有界读取，不用固定 sleep 推测处理完成。
2. 查询使用真实执行的 `SELECT current_setting('work_mem'), current_setting('statement_timeout'), inet_server_addr()::text, inet_server_port(), pg_backend_pid(), pg_is_in_recovery()`；TimeZone 场景另查对应值。work_mem 统一按字节、statement_timeout 按毫秒比较；默认值按“默认值与 RESET 基线”冻结 S0/R0，不硬编码 4MB。
3. 切后端使用 Hint 标签或 sql_parse 合法读写事务/查询。MMR 核对节点地址、端口及 PID；复制组核对主/备及 recovery 状态。同一主机不同实例的 PID 不单独作为身份。
4. 会话重部署场景必须真正得到不同物理后端，池复用场景必须真正复用同一物理后端。可通过占用连接的辅助客户端控制；有界轮询未构造成功则 BLOCKED 并留证，不将未发生切换判成 PASS。
5. 复制组读侧需要健康备库；缺少必要拓扑按 BLOCKED 处理。不得为了造切换而停止开发数据库。Hint 不强制在显式事务中切换物理后端；sql_parse 事务内读转写的特殊行为另做专项，避免扩大本次对齐范围。
6. 不能从 SQL 输出直接宣称内部缓存字段已被验证。用户可见语义通过跨后端/池复用证明；日志作为辅助定位。Q 显式/隐式段和 E 的记录/提交属于不同边界；Hint 与 sql_parse 共用 E 预记录顺序，单语句 Q 与多语句 Q 分别验收；内部更新时间/请求所有权必须完成“缓存写入与架构验收”的产品侧测试，不以时序猜测替代。
7. 不强制两种模式整个回包序列逐字一致；断言各自合法的 ParseComplete、BindComplete、Describe 响应、CommandComplete、SQLSTATE 和最终 RFQ。每个正常完成周期有正确 RFQ，不得多回或漏回；设计要求关闭连接的故障分支按预期断连验收，不虚构 RFQ；ParameterStatus 的相关值及其相对执行边界正确。
8. 未实现的 Hint 对齐行为按真实结果 FAIL；sql_parse 已知边界单独登记，不放宽 Hint 断言。Port/Balance、sql_parse 已知多语句队列槽位覆盖/SET LOCAL 批次限制的修复、P 与 E 间改变事务状态、AND CHAIN、未验证的混合请求调度与 Hint 专用 Flush 等待/屏障、隐式 Q 遇到 BEGIN 的迁移算法、同周期保存点流水线顺序修复、预处理保留=no 的 Extended GUC 跟踪不属于本轮对齐范围。禁用同步及 Extended 透传配置保留兼容基线检查。普通多语句 Q 的事务提交/回滚和失败收口、session pool 安全回收及产品设计 §7.2(14) 的定点故障注入已纳入；系统内存耗尽和无关随机 OOM 仍不在范围内。
9. 每个子场景使用独立客户端和明确基线，在 finally 中恢复/关闭资源。业务断言失败记录后，完成清理，再继续其他独立子场景；不复用失败事务继续验证。遇到代理退出、取消或无法恢复的基础设施错误，停止受影响阶段，剩余检查记录未执行及原因，不计覆盖成功。
10. 综合用例仅在全部必需子场景及两种拓扑检查通过、清理成功时通过。任一业务失败不得被后续成功覆盖；前提不足保留 BLOCKED，基础设施异常保留 ERROR，混合结果保留子场景明细。报告汇总 planned/executed/passed/failed/blocked/unexecuted，并支持通过现有执行上下文选择子场景定位复跑，选择运行不能冒充完整用例覆盖。

## 报告质量契约

遵循 `docs/regression-case-authoring.md` 和 `docs/report-quality-review.md`。参考现有 SQL_PARSE 报告样板截图、`SqlParseExtendedProtocolCase.record_results()` 的分项结果记录，以及 `test_savepoint_failure_keeps_precise_failed_step` 的失败定位要求。旧归档中只有用例 target 作为目的、只有退出码作为实际结果、只检查 OK 标记的报告不作为新增模板。报告契约继续作为验收要求；定向真机报告已生成，尚未完成完整矩阵及浏览器报告验收。

### 报告概览和范围

- 顶部以“测试目的”说明触发条件、验证行为和成功条件，明确当前模式与两个拓扑。目的中的每个承诺映射到具体子场景及断言。
- 展示本次执行身份、时间（Asia/Shanghai）、实际二进制指纹、关键配置、拓扑和必要前置条件。归档本次目的、范围、计划检查及期望；不使用当前源码补造旧执行事实。
- 声明连接与事务范围：客户端 A/B/C 分别何时创建和复用；事务编号、BEGIN/COMMIT/ROLLBACK、自动提交阶段；ROLLBACK TO 在原事务恢复，完整 ROLLBACK 后属于新事务；失败事务 COMMIT 的实际结束标签可能为 ROLLBACK。
- 覆盖表按“拓扑 → 子场景 → 协议/池与挂载状态/故障注入变体”列出计划项、实际执行项和结论。将未执行（executed=false）与已执行但前置阻塞分开统计；未执行步骤按规范显示 BLOCKED/取消原因。统计以同次计划和事实生成，禁止仅计 PASS 项。
- 总结先列具体失败：拓扑、子场景、客户端/事务、步骤、字段、期望、实际；清理结果单列。完整检查未执行完不得显示完整覆盖或全部通过。

### 每个业务步骤必须记录

| 字段 | 本组用例的要求 |
| --- | --- |
| 标题 | 指明行为，如“MMR／Hint／客户端 A：仅 Parse 后 work_mem 保持 8MB”，避免连续使用“执行 SQL”。 |
| 操作 | 实际 SQL；协议检查标明 P/B/Describe Statement/Describe Portal/E/Flush/Sync，以及 statement/portal 名称。复杂交互仅附最小实际代码节选。 |
| 期望 | 在操作前声明精确参数值、行数、SQLSTATE、CommandComplete、RFQ 状态和允许的后端身份；动态默认值先独立采集再冻结为后续期望。 |
| 实际 | 中文展示本步骤实际比较的字段、缺失/重复响应与实际后端身份。work_mem 同时保留原文及标准化字节值；TimeZone 显示实际 ParameterStatus 和查询值。 |
| 断言 | 结构化规则及比较字段，与实际判定实现一致；精确值判定不得标成 output_contains_text。 |
| 判定依据 | 明确哪些字段匹配、哪些不同；无响应、未切后端、错误码不同等原因分别说明，不能只写“符合预期”。 |
| 证据 | 同次执行的 SQL 行、原始报文类型/载荷解码、客户端输出、代理日志及配置可下载；具有稳定子场景/步骤标识，避免交错请求证据串用。 |

准备、连接、配置检查与清理标明 prepare/action/cleanup，不计入业务覆盖。正文展示相关数据，完整 stdout/报文流水留在技术附件；不能每张卡重复全部输出。保留独立“原始报告”和“诊断日志”入口，report.txt 必须为本次实际报告原文。

### 预期错误与中断

- 除零检查预期 SQLSTATE=22012，实际收到对应错误才是 PASS；连接错误 08006 或仅非零退出码不满足期望。
- 多语句 Q 的报告展示实际整段 SQL，并按语句序号解释每段属于哪个事务、哪些语句成功、在哪条语句失败、哪些后续语句没有执行；不得为批次内部没有收到 RFQ 的位置补造就绪状态。Extended 无效 SET 说明失败候选在回滚/保存点恢复后的实测值。显式或隐式段在实际结束标签处独立结算；未被显式结束的隐式段虽收到 SET CommandComplete，仍须等待无错误且 RFQ(I)。已结算段不受后续错误影响，也不重复应用。report 恢复区分最后已提交值与保存点事务视图。
- 混合 Extended 周期报告展示请求编号、statement/portal、动作与实际响应的对应关系，说明“本周期应只有 1 次 ReadyForQuery，实际收到几次”。ErrorResponse 后被忽略至 Sync 的请求属于协议预期，不等同于前提不足 BLOCKED；以独立检查核对“没有成功响应、没有参数副作用”，其检查可 PASS，同时保留被忽略请求的实际事实。
- 故障注入导致的预期断连按注入点判定，不要求该周期产生 RFQ；普通业务周期仍检查唯一 RFQ。必须同时记录无本条成功响应、缓存快照、是否转发、连接关闭及后端不可复用证据，断连本身不足以 PASS。产品侧故障结果与真机业务结果分列。
- 失败事务后 SET 预期 25P02；事务内 DISCARD ALL 预期 25001。记录实际响应和状态，而非将所有错误标成产品失败或所有失败响应标成通过。
- 断言不满足先保存 FAIL 步骤，再结束当前子场景；其依赖步骤保存原定期望并标 BLOCKED，独立子场景在恢复后继续。
- 协议超时、断连、代理启动失败保留已收到的部分报文和启动日志。记录观察到的事实，不能在日志证据不足时断言产品缺陷根因。
- 测试中断也输出覆盖表和清理结果；没有记录的结论显示 UNKNOWN，finished 不能转换为 PASS。

### 单步呈现示例（设计示意，非执行结果）

标题：MMR／Hint／客户端 A：仅 Parse 后 work_mem 未提前生效。

执行内容：发送 P(stmt_set_32mb, SET work_mem='32MB')、Sync；同一客户端通过真实 SELECT 查询 current_setting('work_mem')。

期望：查询恰好一行，work_mem=8MB（8388608 字节）；Parse 周期没有 SET CommandComplete，最终 RFQ=I。

实际展示字段：查询行数、work_mem 原文及字节值、Parse 周期命令标签、RFQ 状态、查询后端地址/端口/PID。实现时全部填入实测值。

失败分析示意：如果实际为 32MB，则明确写“期望 8MB，实际 32MB；尚未发送 Execute，参数已经改变”，并关联对应 P/Sync 和查询证据。不能写“GUC 测试失败”替代差异。

### 实施验收门

1. 计划完整性检查：8 个注册目标，最终矩阵 24 行检查标识，展开两种模式、两个拓扑、协议、池/挂载状态及故障注入分支，并核对产品设计 §7.2 的 16 项追踪；报告计划与实际执行分母一致。分阶段协议顺序、S0/R0 基线和物理后端获取步骤均可追溯。
2. 报告字段检查：每个 verify 步骤有实际操作、expected、actual、assertion、analysis 和同次证据；覆盖说明、连接事务范围已归档。
3. 成功和失败路径测试：参数错误、错误 SQLSTATE、缺失/重复响应、未切后端、子场景未执行、清理失败各能准确记录；失败不能被后续 PASS 或 finished 覆盖。
4. 浏览器检查：顶部目的和范围可见，MMR/主备及子场景可识别；期望/实际/分析可读，错误值原样保留；原始报告、诊断日志和证据下载正常；四主题及窄屏可读。页面不直接显示内部 JSON/工件路径，不用颜色作为唯一结论。
5. 真机报告审阅：两种模式均核对实际执行报告与原始证据；产品失败如实保留。通过场景与受控不匹配的报告验证分开标识，模拟数据不得冒充真机 PASS。
6. 对现有渲染摘要/状态映射核对 BLOCKED、ERROR、未执行、清理统计，不能仅依赖通用“未通过”计数满足本组覆盖摘要；如公共报告能力不足，在公共层补齐，不编造产品结果。
7. 产品侧缓存边界、失败终止处理、执行门控、后端安全回收、保存点错误恢复及 owner 测试全部有可复核结果；源码 review 完成调用顺序/所有权检查。仅平台 SQL/协议回归通过，不能宣称两个缓存问题已完整验收。sql_parse 按改造前基线证明结果与响应顺序不变，不强求满足 Hint 的新内部顺序。

## 平台落地

- 新建 `products/fbasecman/guc_alignment_native.py`，实现参数化原生用例；登记到 `cases.py` 与 `regression/catalog.json`，保留已有 18 条 GUC 用例。
- 复用平台 `clients/pgwire.py` 报文原语、CaseContext 生命周期与证据。通用的 ParameterStatus 解码/分阶段读取缺口补平台 SDK；GUC 场景断言留在产品。
- 配置渲染需支持明确选择 mmr_group/rep_group，以及 transaction/session pool、预处理保留开关 yes/no 和后端挂载状态分支；Q 配置矩阵和 Extended 透传兼容基线分开登记。目前非 none 模式只授权 mmr_group，不能直接将复制组名字作为 database 而不补授权与就绪检查。
- 不消费旧公共测试表，不创建永久业务对象；每条用例启动自己管理的代理、客户端和辅助连接，统一清理，保留代理日志和失败报文。
- 每个报告步骤记录模式、拓扑、连接身份、SQL/协议动作、期望、实际值、SQLSTATE、事务状态与判定依据。测试过程 JSON 和原始报文序列可下载。
- 实施先落地未 Execute、COMMIT/ROLLBACK、ParameterStatus 四类代表场景，在两种模式真实验证；再扩展全部矩阵。平台单测检查成功和失败判定/证据，真机验证检查产品行为，产品侧测试验证缓存写入边界、公共本地提交顺序及失败终止、执行门控、安全回收、owner 和定点故障；三类结果分别报告。故障注入需产品侧可控处理器/failpoint 支持；缺少入口则保留未执行事实，不以平台模拟报告替代产品验证。

## 实施状态与本次 review 发现

### 已落地的入口和事实记录

- `products/fbasecman/guc_alignment_native.py` 已实现 4 个综合场景 × 2 模式的 8 个原生入口，登记到 cases.py/catalog.json；原有 18 个 GUC 用例保留。矩阵 23 个检查标识全部进入计划；每个综合场景另有必需 product_cache_boundaries 检查，避免只运行 SQL 就宣称内部缓存已验证。
- 已实现裸协议的 Q、P/B/Describe Statement/Describe Portal/E/Close/Flush/Sync 驱动，记录完整响应类型、载荷、所有 CommandComplete、SQLSTATE、RFQ 和 ParameterStatus；失败及断连保留已收报文。新增 SDK 原语处理通用协议，产品参数比较和场景断言留在产品。
- 已实现事务提交/回滚、RESET/RESET ALL、失败事务实际 ROLLBACK 标签、SET LOCAL、多语句 Q 的隐式/显式段、分步保存点及 report 恢复、候选隔离、重部署和同进程两模式交错的业务检查。混合组合需要实际 sql_parse 基线支持，缺失双模式用户则 BLOCKED。
- 每次执行归档完整计划、构建指纹、配置、原始报文、代理日志、覆盖汇总和 §7.2/§8 映射。计划分母保留配置、拓扑、未执行及选择复跑项；业务 FAIL 不被后续 PASS 覆盖。产品侧内部项缺失时保持 BLOCKED，不能以单测模拟数据或最终 SHOW 冒充验证。

### 测试驱动已补齐，产品开发前即可执行

| 必需项 | 可执行检查 |
| --- | --- |
| P/B/D 正式和工作缓存、E 提交、事务 Q/E 工作缓存与提升 | product_cache_boundaries 使用当前产品对象链接测试拦截器，直接记录真实缓存 mutation/copy，不需预先修改业务源码。未符合目标为 FAIL。 |
| 本地构造/apply/入队；E/Q 预记录、pending、outstanding、转发失败 | local_commit_failure/execute_registration_failure 已实现独立进程定点注入；验证触发事实、响应、缓存、代理存活及失败收口。不触发为 FAIL，无永久 BLOCKED。 |
| 同进程双模式隔离 | 自动创建本次独占临时角色、代理规则，SDK 结束时清理；普通业务及产品侧候选 owner 检查分别记录。 |
| sync=no / Extended reserve=no | compatibility_scope 已实现 10 个拓扑/开关/协议检查。 |
| 开发前后 sql_parse 保护 | 混合响应保存实际开发前基线，后续运行精确比较响应顺序、SQLSTATE、RFQ 和参数值；原有不支持组合单列限制，不为 Hint 扩大范围。 |
| 完整矩阵和测试结果 | 用例代码具备全范围计划、执行、失败继续、清理和汇总；开发前失败是预期。当前基线产品自身问题仍如实 FAIL，不以断言适配修改产品。浏览器样式验收不阻止测试驱动用于开发。 |

测试工具位于 guc_instrumentation.py 和 assets/guc_probe_wrap.c：读取被测 CMake link.txt/flags.make，复用产品对象生成独立测试代理，仅链接 --wrap 取证/注入。产品业务源码未修改；开发后正常重建产品，再运行同一测试入口。测试拦截器额外堆栈与工作线程配置仅作用于测试代理。

### 真机发现的配置范围冲突

当前 `sources/frontend.h:16` 定义 Hint 标签完整前缀为 `SET SESSION CHARACTERISTICS AS TRANSACTION READ `；驱动使用完整 SQL，不发送注释中的 SET READ ONLY/WRITE 简称。

当前 `sources/rules.c:1846` 及实测启动日志明确拒绝 `pool=session` 与 `pool_reserve_prepared_statement=yes` 的组合（错误原文为 prepared statements support in session pool makes no sence）。因此设计要求的这部分 session 矩阵在当前构建无法执行。保留原配置及既有限制 SKIPPED 事实，不静默改为 reserve=no 或 transaction pool；需产品方案明确该分支作为当前限制还是另行支持，不能为本次 Hint 对齐擅自修改公共配置约束。此外 `sources/rules.c:2597` 和实测日志明确 Hint 只允许 transaction pool，因此 Hint 的 session+reserve=no 也无法执行；两类限制分别留证。sql_parse 的 Q session+reserve=no 仍按计划执行。

新增配置按真实 MMR group 名称及备库 application_name 渲染，不硬编码旧环境 g1/pg_240/pg_250；多个 MMR group 必须显式选择。备库名称可由 extra_nodes 的 application_name 指定，默认按 pg_3/pg_4，启动和路由检查独立留证。

### 定向真机结果（不是完整验收）

- Hint / MMR / transaction pool / reserve=yes：仅 P+Sync、不 E，原客户端查询期望 work_mem=8MB，实际32MB；真实 FAIL，报文与后端身份已归档。执行记录 `run_20261008_130000_93227e7fccf2`。
- sql_parse 同配置基线：仅 P+Sync 期望 ParseComplete+RFQ，实际只有 RFQ；真实 FAIL，保留协议差异，不能据此宣称 sql_parse 基线通过。执行记录 `run_20261008_130040_9dcfe9ae719c`。
- Hint 事务提交后换后端：MMR / transaction pool 的 Q reserve=yes/no 和 E reserve=yes 分支均期望 work_mem=32MB，实际8MB，真实 FAIL；执行记录 `run_20261008_130614_a7d112da91a4`。本次检查已使用 frontend.h 定义的完整路由标签 SQL，排除了发送简称导致的语法错误。
- 上述记录均为选择复跑，session+reserve=yes 记录既有配置限制，未选拓扑/场景保留未执行；不将局部结果推广为全部 16 项或 §8 验收完成。此前首批新增及相关框架单测共 68 项通过，证明驱动/判定/证据契约，不代表产品 GUC 修复通过。

### 开发前运行方式

参见 `products/fbasecman/regression/suites/guc/README.md`。8 个入口用于业务修复的失败到通过验证；全部支持范围内业务和内部检查通过时可以 PASS，不再由硬编码内部阻塞阻止通过。配置拒绝和实际 sql_parse 既有限制单列 SKIPPED，不计为通过覆盖；环境缺失才 BLOCKED。不要删除冻结的开发前 sql_parse 基线再接受开发后变化。

2026-10-08 补齐后新增及相关框架单测 75 项通过，其中含完整模拟执行能 PASS 的回归，防止测试永久失败。完整真实基线按两模式八入口执行；第一轮全部报 FAIL、没有执行器 ERROR/BLOCKED，结果是被测产品状态，不是测试代码单测结果。完整矩阵实际结果保存于 output/fbasecman/guc-tdd/final-before；默认值取证修正后的复核结果位于 output/fbasecman/guc-tdd/verified-before/results.json。

最终复核完成：两种模式全部 8 个入口真实运行，均为 FAIL；支持范围内检查共 132 项 PASS、124 项 FAIL，执行器 ERROR=0、前置 BLOCKED=0；164 项既有配置/基线限制单列 SKIPPED，不计通过覆盖。开发前结果及分项轨迹归档于 verified-before/results.json，各用例目录保留 events、coverage、原始 wire 和真实 cache/fault trace。测试代码单测 75 项通过，产品源码未改。

## Hint 用例与 sql_parse 测试修正同步（2026-10-08）

本节修正此前报告解释：sql_parse 的部分失败来自测试连接与场景构造，不能全部称为产品失败。原始报告保留历史事实，当前结论以修正后的运行记录为准。

- RESET 默认值通过独立直连、options='' 的干净连接取得；平台 SQL 会话的启动 statement_timeout 会污染 current_setting 和 reset_val，两种模式都不得用其作为业务默认值。
- GUC 操作仍按场景使用 Q 或 E；完成 Sync 后的参数/物理身份观测统一使用真实 Simple Q，避免把观察动作额外变成 PreparedStatement 缓存复用专项。P/B/D/E 候选和 portal 的专属断言仍保留。
- 事务池继续核对真实后端切换和参数；session pool 检查原物理连接保持及参数，不强求 session pool 交接/换后端。此修正共用于两模式，不能据 pinned 后端未变化判产品失败。
- Hint report/DISCARD/兼容检查需要固定写侧时，按 `Q(SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE)` → `BEGIN` → 被测事务操作执行；sql_parse 使用 BEGIN READ WRITE。标签发生在事务外，不把 sql_parse 的语法与状态机直接套入 Hint。
- 用户明确暂不处理 sql_parse 既有问题。因此 DISCARD 保留独立成功、事务内 25001 拒绝、ROLLBACK 后参数恢复；不再追加“拒绝回滚后再次 DISCARD/复用 PreparedStatement”的既有组合专项，该排除在步骤中标 SKIPPED，不算通过覆盖。没有排除普通 GUC 的错误候选清理、COMMIT/ROLLBACK 或两个核心缺陷断言。

Hint 开发前仍要求仅 P 不 E 时 work_mem 保持8MB，以及事务提交后换后端仍保持32MB；测试修正不会将这两个期望改成旧代码的错误值。对应实测及原始 wire 保存于 output/fbasecman/hint-synchronized-audit。

### 已确认既有问题：DISCARD-Q-NO-RESERVE

最小请求为建连后仅发送事务外 Q `DISCARD ALL`。同一个被测二进制的结果：Hint/no 与 sql_parse/no 均断连；Hint/yes 与 sql_parse/yes 均得到 CommandComplete(DISCARD ALL)、唯一 RFQ(I)。完整四组证据保存于 output/fbasecman/discard-q-root-audit/results.json。

源码原因：普通 Q 以 internal_kind=NONE、global_entry=NULL 调用 fb_add_outstanding_request；fb_outstanding_should_track 在 reserve=no 时返回 false，因此返回 OD_OK 但没有 Q 节点。后续 fb_frontend_begin_discard_all 必须把该 Q 标成 DISCARD_ALL，找不到节点返回 OD_STOP，原 Q 尚未交给正常发送路径。od_server_sync_request 已先增加请求计数，因此断连清理随后出现 not responded/cancel limit 日志；该日志是后续现象，不是根因。

按用户“sql_parse 也有的问题先不管”的授权，只将 routing_and_discard_boundaries 的 Q+reserve=no DISCARD 检查记为既有限制 SKIPPED，保留已完成路由检查及排除原因；不会排除 tx_set_commit 等其他 Q+reserve=no 检查，不改变 reserve=yes 的 Q/E DISCARD 断言。产品代码未修改。

### 网页 cman-mmr 环境启动问题已修正

2026-10-08 的 run_20261008_151756_6d4b0dc3da7c 报 ERROR，原因为测试添加的 guc_h 临时用户未获 pg_hba 放行，groupcheck 连接被拒绝，代理启动退出；并不是 sql_parse 业务断言失败。sql_parse 的普通 Extended 基线现直接使用 postgres，不依赖额外 Hint 用户。真正双模式检查先探测临时角色到全部后端的连接，失败立即清理角色，不注入无效代理规则，普通独立用例继续；不擅自修改 HBA。

复制 application_name 在环境未声明时从 pg_stat_replication 获取并核对唯一候选，兼容网页 pg_240/pg_250 与开发 pg_3/pg_4。已使用相同 cman-mmr 的11011/11021与11012/11022配置真实复跑 guc.extended_boundary_sql_parse，CLI 完整结果 PASS，26.212秒。旧任务报告保留原始 ERROR，新执行不覆盖历史。

### 新增 8 用例真实网页验收（2026-10-08）

已在 cman-mmr 页面用 Playwright 实际逐条点击执行8个新增入口，另复跑保存点 sql_parse一次，共9个任务。最新结果：sql_parse extended_boundary/transaction_sync/savepoint_report 为PASS；backend_redeploy的24项业务检查通过、无业务失败，但2项同进程mode_owner_isolation因HBA不放行临时用户为BLOCKED，网页任务标FAILED。Hint四条均为开发前业务FAIL，两个目标缺陷有实际值及wire证据。

网页暴露的报告500已修复：报告展示将结构化 expected/actual 转成可读文本，保留 structured_* 原始事实；历史事务范围字符串兼容转换为数组，新用例目录也使用正确的事务数组。session pool 的 report 参数重复分支改为真实事务设置UTC后提交，避免本地SET仅修改前端状态的准备误判。网页最新报告均可打开，无pageerror；相关单测89项通过。未修改产品业务代码或HBA，不能将阻塞项宣称完整通过。

完整网页任务ID、状态、日志与截图位于 output/fbasecman/browser-guc-check/，最新汇总为 final-results.json 与 SUMMARY.md；首次保存点失败报告保留，最新通过截图与任务位于 timezone-recheck/。执行脚本 frontend/tests/fbasecman-guc-execution.mjs 不mock请求，支持指定环境、目标与证据目录。

### 用户决定：只使用现有 postgres 账号（2026-10-08）

用户明确“不用搞2个账号”，覆盖之前的双模式临时角色安排。当前执行不创建guc_h/guc_s用户，不修改HBA。sql_parse基线直接使用当前代理与postgres；Hint需要对照时另起同配置sql_parse测试代理，仍用postgres，并在结束时关闭。

mode_owner_isolation 的同进程交错专项明确标SKIPPED、不声称验证；其余普通GUC、P/B/D/E候选、事务提交回滚、ParameterStatus、会话A/B/C（独立连接而非不同账号）、故障和后端重部署断言保留。两个独立代理不能作为同进程owner隔离证据。权限不再是业务回归前提，原临时HBA方案未应用。

网页重跑使用 frontend/tests/fbasecman-guc-execution.mjs，证据目录 output/fbasecman/browser-guc-check/single-account-recheck。

单账号方案网页复跑完成：backend_redeploy_sql_parse 任务 c41512d7-d9f6-4a9e-9fd2-e310e908184c 为SUCCEEDED；Hint任务 b1af85bc-81f8-42dc-baa4-c6e43c822d90 仍为业务FAILED。sql_parse四条新增入口最新网页结果均通过，Hint四条保持开发前失败；没有HBA修改或新账号，同进程双模式交错专项依用户指示SKIPPED。独立sql_parse对照代理使用postgres的真实冒烟也通过，结束时代理已关闭、端口上下文已恢复。最新网页汇总：output/fbasecman/browser-guc-check/final-results.json 与 SUMMARY.md。

### 报告改为业务步骤（2026-10-08）

正常 Q/P/B/D/E 收发不再作为独立业务验证卡片，归入执行与诊断记录；协议失败和预期错误仍保留必要断言并可见。同一次查询比较 work_mem/statement_timeout/TimeZone 时合并为一张参数卡片，说明前序操作、逐参数期望与实际及物理后端，判定与原断言不变。准备基线按prepare标记，不算业务成功覆盖。

兼容旧报告时，展示层也将正常协议观察折叠、参数卡片分组并说明具体差异；grouped_checks/structured_*保留原始事实，report.txt与wire证据不改写。默认失败检查优先，最多渲染当前页50项；原始报告、全部步骤和诊断日志继续可访问。新增事务同步sql_parse真实网页复跑仍为PASS；8个新增报告全部通过真实浏览器翻页、期望/实际、原始报告和日志检查，没有pageerror。相关单测92项通过，前端构建通过。
