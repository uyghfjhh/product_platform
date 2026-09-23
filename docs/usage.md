# 使用与配置

## 环境登记

在“产品与环境”选择产品并填写环境 ID、主机、端口、数据库名和用户。部署时另填 `pgcluster` YAML 路径与目标，例如 `mmr.fbasecman_regress`。

fbasecman 回归可在“部署管理”点击“生成方案”，填写 MMR1 主端口、远端 PGDATA 根目录和 License 路径。平台会生成：

```text
data/profiles/<环境 ID>/pgcluster.yaml
data/profiles/<环境 ID>/regress.override.yaml
data/legacy_cman/<环境 ID>/output/
```

生成方案只写平台本地文件并校验配置。部署前核对端口、PGDATA 与目标机器。部署任务实际调用 `pgcluster`；其 `.pgcluster-managed` 标记是清理与实例管理边界。

## fbasecman 测试

部署完成后点击“准备测试夹具”，创建测试数据库、角色、表、多活组与 `test_context.yaml`。此步骤会修改数据库，应只在专用测试环境执行。随后在“自动化测试”运行单例或整套件，使用“报告”查看当前结果和原始证据。当前框架还支持从既有 `fbasecman_regress_v2` 输出目录查看报告；选择环境后优先使用该环境的隔离产物。

“重跑失败项”只重跑隔离输出目录中有 FAIL 摘要的用例。单例或套件执行时旧代码仍在隔离进程内运行；新平台不导入旧框架的 `framework` 包。

## License

默认读取 `/home/postgres/fly_dev/fd_licenser/keys` 中的 `v1.N` 加密密钥，并从同仓库的 `config.json` 读取产品名称和默认版本。可用 `PRODUCT_PLATFORM_LICENSE_KEYS` 和 `PRODUCT_PLATFORM_LICENSE_VENDOR` 覆盖。页面请求只生成和下载文件，不创建后台申请、审批或签发队列。

生产密钥的旧格式兼容应以目标产品验签为最终依据。当前自动测试只使用临时测试密钥，没有读取或导出真实私钥。

## API 与数据

接口说明位于服务 `/docs`，当前 API 版本前缀是 `/api/v1`。平台数据目录可以备份；运行中的 SQLite 使用 WAL，备份时使用 SQLite 在线备份工具或先停止服务。任务日志位于 `data/operations/`，单个用例的报告和证据位于 `data/legacy_cman/<环境 ID>/output/runs/`。
