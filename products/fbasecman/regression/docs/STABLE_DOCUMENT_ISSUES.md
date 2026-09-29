# Stable 旧文档问题

## 汇总

- 已确认旧文档问题：4

## SD-001：旧配置示例已被配置文件重构淘汰

旧模板使用 `write_datasource_names/read_datasource_names/primary_replica_maps` 等字段，
当前产品提交 `2a1da0f820dcf241743bf720c587654dd82f9398` 已改为 backend cluster 模型。
当前 stable 使用新模型渲染，不复制旧模板。

## SD-002：旧 JDBC 结果无条件打印 PASS

旧 `PreparedLeakMain` 最终始终输出 `RESULT: PASS`，即使 `failures` 或分类 failure
计数非零。当前实现要求所有失败计数为 0，否则输出 FAIL 并返回非零。

## SD-003：旧 console workload 循环执行 RELOAD

旧 `test7.sql` 在常稳期间循环执行 `SHOW NODE_STATUS; RELOAD;`。RELOAD 是配置管理
动作，不是无副作用 console 稳定性负载，并可能在并发压力下干扰 group check。
当前改为 `SHOW NODE_STATUS/SHOW THREAD_STATUS/SHOW POOLS`。

## SD-004：旧 JDBC GUC 压力包含驱动拒绝的 DateStyle

旧 `PreparedLeakMain` 在已建立的 JDBC 连接中循环执行
`SET DateStyle = 'ISO, MDY/DMY'`。PostgreSQL JDBC 驱动收到对应 ParameterStatus 后会
主动关闭连接并报错，因为驱动要求 DateStyle 使用其可解析的 ISO 格式；旧实现又因
无条件打印 PASS 隐藏了该失败。当前 workload 保留 `application_name`、`TimeZone`、
`extra_float_digits`、`enable_seqscan` 的 SET/SHOW/RESET 以及 `RESET ALL` 压力，删除
不符合驱动约束的 DateStyle 变更。
