"""Readable JDBC observations; formatting never changes recorded verdicts."""

import re

JDBC_LABELS = {
    "PARAM_VALUE": "参数查询返回值",
    "ROLLBACK_ERROR": "回滚前的 SQL 错误码",
    "ROLLBACK_VALUE": "回滚后新事务的查询返回值",
    "ROLLBACK_RECOVERY": "回滚后的恢复标记",
    "COMMIT_ERROR": "结束失败事务前的 SQL 错误码",
    "COMMIT_VALUE": "结束失败事务后，新事务的查询返回值",
    "COMMIT_RECOVERY": "结束失败事务后的恢复标记",
    "READ_PORT": "读请求的实际后端端口（诊断信息）",
    "WRITE_PORT": "写请求的实际后端端口（诊断信息）",
}


def jdbc_observation(name, value):
    rendered = str(value)
    if name in {"ROLLBACK_ERROR", "COMMIT_ERROR"} and rendered == "22012":
        rendered += "（除零错误）"
    return JDBC_LABELS.get(name, name) + "：" + rendered


def readable_jdbc_observations(text):
    if not isinstance(text, str):
        return text
    lines = [line.strip() for line in re.split(r"[；\n]", text) if line.strip()]
    fields = [re.fullmatch(r"([A-Z_]+)=(.*)", line) for line in lines]
    if not fields or any(field is None or field[1] not in JDBC_LABELS for field in fields):
        return text
    return "\n".join(jdbc_observation(field[1], field[2]) for field in fields)
