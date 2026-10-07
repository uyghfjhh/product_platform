# 常稳执行目标与性能对比

回归和常稳页的「执行环境 → 配置被测 fbasecman」共用一份环境配置。
可执行文件和 License 目录是平台控制机上的绝对路径，不是数据库主机路径。
页面显示真实版本、解析后的路径和 SHA256；环境未保存时明确显示服务器默认配置。
保存后环境配置优先，不会被 PRODUCT_PLATFORM_FBASECMAN_BIN 等默认值替换。
回归命令将选择写成独立执行快照，注入 native 与 runtime 用例；常稳请求保存相同构建信息。
负载审阅保存二进制 SHA256，启动和实际执行时重新核对；重编译后需重新审阅。

## 连接方式

- **fbasecman 代理**：fbasecman 产品默认选项。启动本轮独立代理，动态分配监听和 metrics 端口，使用独立配置、PID、locks 和日志，预热 3 秒后执行负载，结束或取消时停止本轮代理。
- **数据库直连**：连接登记的 host/port，不启动代理。
- **直连 / fbasecman 对比**：先直连、后代理，各预热 3 秒，测量时长、SQL、驱动、并发和长短连接选项一致。总测量时长为配置时长的两倍，不包括预热、编译与启动。

参考旧 stable.sh 的独立生命周期、64KB 协程栈、GUC 同步、事务连接池和低噪声日志；不直接调用旧脚本或搭建/删除数据库。
这里的查询性能测试使用 single group 固定到登记后端，以便测代理开销。
MMR 多节点路由、读写切换、HA 并发变更、PreparedStatement 泄漏等旧 stable 专用场景仍单独列为未接入，不能由 SELECT 1 的结果替代。

启动前后端直连查询返回 inet_server_port/current_database/system_identifier。
代理就绪后、每阶段前后验证这三个值完全一致，同时保存 SHOW DATASOURCES、SHOW POOLS、SHOW VERSION 证据。
连接/身份验证或任一阶段失败即停止，不执行下一阶段、不生成有效性能百分比。
被测用户须具有 pg_control_system() 查询权限；预检失败时明确报错，不绕过后端身份验证。

## 指标

吞吐损失百分比 = (直连 TPS − 代理 TPS) / 直连 TPS × 100%。
平均延迟增幅 = (代理平均延迟 − 直连平均延迟) / 直连平均延迟 × 100%。
负值保留，表示代理阶段更快；直连延迟为 0 时不计算延迟百分比。
仅双方执行成功且指标完整时计算。限速负载的 TPS 可能受到目标速率上限约束，不能据此推断最大吞吐开销。
顺序单轮测量受到缓存、CPU、后台活动与执行顺序影响；短时试跑用于功能验证，正式评价需更长时长并多轮复测。

每项任务证据目录：`output/fbasecman/<environment>/runs/<task>/workload/`。
保存 request.json、fbasecman.conf、fbasecman.log、connection-evidence.json、proxy-monitor.json、直连/代理各自原始采样和 result.json。
页面分别展示直连与代理曲线、百分比、代理 PID/端点、RSS 与 CPU（100% 为一个核）。
环境数据库/主机采样与代理进程采样分别展示，不将数据库主机 CPU 冒充代理 CPU。
