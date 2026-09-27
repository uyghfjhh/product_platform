from framework.assertions import sql_fails
from framework.steps import sql_step


CASE = {
    "id": "mmr.node_function_control.two_phase_change_unsupported",
    "name": "多活节点 two_phase 不支持运行时修改",
    "document": "多活功能测试文档.md",
    "section": "9.5 测试三",
    "group": "node_function_control",
    "fixtures": ["cluster"],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "mmr1 为健康可写的多活成员，fdd_mmr 已安装。",
        "本用例使用文档中的无效参数 twophase；函数会在修改任何节点元数据前拒绝请求。",
    ],
    "steps": [
        sql_step(
            "按文档尝试运行时修改 twophase 功能", "postgres",
            "SELECT fdd.alter_node_info('twophase', 'mmr1', 'true', true)",
            "SQL 执行失败，错误包含 unsupport param_type twophase，且提示合法参数名",
            sql_fails('unsupport param_type twophase, must be "failover","streaming" or "two_phase".'),
            node="mmr:mmr1"),
    ],
    "teardown": "函数在参数校验阶段失败，不修改节点元数据、订阅或复制槽，无需清理。",
}
