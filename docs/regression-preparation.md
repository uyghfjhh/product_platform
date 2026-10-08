# 回归测试准备的职责与页面入口

## 2026-10-08 核查结论

部署页原有“准备测试夹具”不是平台通用初始化，而是 fbasecman 的
`tests.prepare_fbasecman` 产品动作。产品 Provider 调用
`products/fbasecman/deployment/fixture.py`，通过公共任务执行和环境资源锁运行。

不同测试页的现状：

| 测试页 | 准备机制 | 页面入口 |
| --- | --- | --- |
| FBase 多活 | `cases.py` 按声明执行 requirements、session fixtures 和 case fixtures；资源恢复由 SDK 清理管理 | 不添加全局准备按钮 |
| FBase 等保 | 同一产品执行内核，根据具体用例声明准备账号、表和参数，并登记恢复 | 不添加全局准备按钮 |
| fbasecman 回归 | 部分用例自行准备；部分产品 runtime 仍消费公共测试对象和 test_context.yaml | 在绑定环境下显示“fbasecman 公共测试准备” |

fbasecman `provider.py` 对直接执行的 `NATIVE_CASES` 不要求上下文文件；
其他目标（包括套件、失败重跑及 all）仍检查文件存在。
`runtime_cases.py` 的 global_cache setup 直接读取文件，handover runtime
也检查文件存在。`case_runtime.py` 部分元数据已改为实时查询，
`native.py` 中 GUC 等用例已有专属准备；不能据此认为公共准备完全无人使用，
也不能把所有用例都标为依赖它。

## 公共准备实际影响

- 在两个 MMR 主节点创建缺失的测试角色、postgres 库测试表和 test_db。
- 替换公共 MMR 视图、授予查询权限，并同步 u3 的密码散列。
- 在 test_db 安装 fdd_mmr／适用的 Citus 扩展，创建或加入测试 MMR 组，插入测试数据。
- 创建物理复制槽，修改备库 primary_conninfo、primary_slot_name 并重载。
- 采集组 UUID、角色密码散列、系统标识和二进制指纹，写入环境专属上下文。

同名对象会被复用，公共资源长期保留，不是每次执行结束就清理的临时资源。
准备脚本部分采集异常返回空值，部分备库配置失败只记警告；因此文件存在或
准备任务成功不证明全部前置条件满足。页面只显示“已记录测试上下文”，
不显示“全部就绪”。用例判定与既有准备行为保持。

## 本次页面调整

从部署页移除入口，通过产品前端的 TestPreparation 扩展，只在 fbasecman
回归 profile 显示公共准备。提交前列出修改范围并要求确认，目标为 all、
cluster 为 cman，使用现有 OperationService、绑定校验、任务和资源锁。
页面在任务状态变化后刷新上下文状态，也支持手动刷新；环境有未结束任务时
禁用准备。没有自动对已登记数据库执行准备。

## 后续拆分边界

进一步细化需要逐用例确认消费关系，将测试库／认证账号／复制配置／只读
上下文采集拆开。用例专属资源归 setup/cleanup，共享资源明确 suite/session
范围；缺前提保持 BLOCKED。不能仅把现有整套准备复制到每个测试页或每条用例，
也不能在删除公共准备前只检查上下文文件而不验证真实消费对象。
