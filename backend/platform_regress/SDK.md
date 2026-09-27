# Platform regression SDK

产品回归代码只依赖 `platform_regress.sdk`，不依赖平台 Web API、SQLite
模型或另一个产品的测试框架。

## 最小用例

```python
from platform_regress.sdk import CaseContext, ProductCase


class VerifyCluster(ProductCase):
    def run(self, context: CaseContext) -> bool:
        result = context.sql("node-1", "select 1")
        passed = result.rows == (("1",),)
        context.step("cluster-ready", "校验集群可用", status="PASS" if passed else "FAIL")
        return passed
```

`CaseContext` 统一提供 SQL、命令、取消检查、步骤事件和证据归档。
环境节点由平台绑定的 pgcluster 环境注入，产品代码不创建 PostgreSQL
数据目录，也不负责环境生命周期。

## 结果和失败语义

- `True` 表示业务 PASS，`False` 或断言异常表示 FAIL。
- `Blocked` 表示环境前置条件不满足，平台会保留为 BLOCKED。
- `Cancelled` 表示用户取消。
- 每次执行都会生成结构化结果和证据目录，可在产品包删除后继续查看。

旧产品迁移期间可以在产品自己的 `cases.py` 保留兼容执行器，但必须通过
`CaseContext` 发布新 run 的结果和证据；平台不会导入产品的旧 framework。
