"""Prisma Studio BFF 协议端点。

协议（@prisma/studio-core/data/bff 客户端线格式）：
- POST JSON：procedure ∈ query / sequence / transaction / sql-lint / query-insights
- 查询参数用 kysely 的 $N 占位符，需翻译成 psycopg 的 %s（跳过字面量与注释）
- 响应为 Either 元组 [error, result]；错误序列化为 {name, message, errors?}
- query-insights 不实现（客户端未开启该视图则不会调用）
"""

import json
from datetime import date, datetime, time
from decimal import Decimal

from .database import psycopg, tuple_row

_STATEMENT_TIMEOUT_MS = 15000
_MAX_PARAMS = 256


def _translate_parameters(sql):
    """kysely $N 占位符 → psycopg %s；跳过引号、美元引用、注释。

    返回 (翻译后 SQL, 占位序号顺序)——%s 顺序绑定，调用方须按序重排参数，
    使 $2 先于 $1 出现或重复引用同一参数时绑定仍然正确。
    """
    out = []
    order = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n and sql[j] != "'":
                j += 2 if sql[j] == "\\" else 1
            j += 1
            out.append(sql[i:j])
            i = j
        elif ch == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            out.append(sql[i:j])
            i = j
        elif ch == "-" and sql[i : i + 2] == "--":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(sql[i:j])
            i = j
        elif ch == "/" and sql[i : i + 2] == "/*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(sql[i:j])
            i = j
        elif ch == "$":
            tag_end = sql.find("$", i + 1)
            tag = sql[i : tag_end + 1] if tag_end != -1 else ""
            if tag and tag != "$" and all(
                c.isalnum() or c == "_" for c in tag[1:-1]
            ):
                # $tag$ 美元引用起始（如 $$、$func$）
                close = sql.find(tag, i + len(tag))
                if close == -1:
                    out.append(ch)
                    i += 1
                else:
                    out.append(sql[i : close + len(tag)])
                    i = close + len(tag)
            elif i + 1 < n and sql[i + 1].isdigit():
                j = i + 1
                while j < n and sql[j].isdigit():
                    j += 1
                order.append(int(sql[i + 1 : j]))
                out.append("%s")
                i = j
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), order


def _encode_value(value, column, transformations):
    if value is None:
        return None
    if transformations.get(column) == "json-parse":
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        # int8 超出 JS 安全整数按字符串返回，防精度丢失
        return value if -(2**53) < value < 2**53 else str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    return value


def _serialize_error(exc):
    return {"name": type(exc).__name__, "message": str(exc)}


def _connect(environment, autocommit=True):
    return psycopg.connect(
        host=environment["host"],
        port=environment["port"],
        dbname=environment["database_name"],
        user=environment["database_user"],
        connect_timeout=5,
        options="-c statement_timeout=%d" % _STATEMENT_TIMEOUT_MS,
        autocommit=autocommit,
        row_factory=tuple_row,
    )


def _apply_schema(cursor, schema):
    if schema:
        cursor.execute("SELECT set_config('search_path', %s, false)", (schema,))


def _run_query(cursor, query):
    sql = query.get("sql") or ""
    parameters = query.get("parameters") or []
    if len(parameters) > _MAX_PARAMS:
        raise ValueError("参数数量超出限制")
    translated, order = _translate_parameters(sql)
    try:
        bound = tuple(parameters[index - 1] for index in order)
    except IndexError:
        raise ValueError("占位符序号超出参数数量")
    cursor.execute(translated, bound)
    if cursor.description is None:
        return []
    columns = [column.name for column in cursor.description]
    transformations = query.get("transformations") or {}
    rows = []
    for row in cursor.fetchall():
        rows.append(
            {
                column: _encode_value(value, column, transformations)
                for column, value in zip(columns, row)
            }
        )
    return rows


def _lint_sql(environment, schema, sql):
    """EXPLAIN 解析+规划；事务内执行并强制回滚——即使输入以 ANALYZE
    开头使语句真实执行，也不会落盘。错误映射为诊断。"""
    try:
        connection = _connect(environment, autocommit=False)
        try:
            with connection.cursor() as cursor:
                _apply_schema(cursor, schema)
                cursor.execute("EXPLAIN " + sql)
                cursor.fetchall()
        finally:
            connection.rollback()
            connection.close()
        return {"diagnostics": []}
    except Exception as exc:
        diagnostic = {
            "from": 0,
            "to": len(sql),
            "severity": "error",
            "source": "postgres",
            "message": str(exc).split("\n")[0],
        }
        position = getattr(getattr(exc, "diag", None), "statement_position", None)
        if position:
            try:
                # statement_position 以 EXPLAIN 前缀后的 SQL 计——减前缀长度回原 SQL
                pos = max(0, int(position) - 1 - len("EXPLAIN "))
                diagnostic.update({"from": pos, "to": min(len(sql), pos + 1)})
            except (TypeError, ValueError):
                pass
        return {"diagnostics": [diagnostic]}


def studio_dispatch(environment, body):
    """执行 BFF procedure；返回 Either 元组（list 两项）。"""
    if psycopg is None:
        return [{"name": "RuntimeError", "message": "未检测到 psycopg 依赖库"}, None]
    if not isinstance(body, dict):
        return [{"name": "ValueError", "message": "请求体需要 JSON 对象"}, None]
    procedure = body.get("procedure")
    try:
        if procedure == "query":
            query = body.get("query")
            if not isinstance(query, dict):
                raise ValueError("缺少 query 对象")
            with _connect(environment) as connection:
                with connection.cursor() as cursor:
                    _apply_schema(cursor, body.get("schema"))
                    return [None, _run_query(cursor, query)]
        if procedure == "sequence":
            sequence = body.get("sequence") or []
            if len(sequence) != 2:
                raise ValueError("sequence 需要恰好两个查询")
            with _connect(environment) as connection:
                with connection.cursor() as cursor:
                    _apply_schema(cursor, body.get("schema"))
                    results = []
                    for query in sequence:
                        try:
                            results.append([None, _run_query(cursor, query)])
                        except Exception as exc:
                            results.append([_serialize_error(exc)])
                    return results
        if procedure == "transaction":
            queries = body.get("queries") or []
            if not queries:
                raise ValueError("transaction 需要至少一个查询")
            with _connect(environment, autocommit=False) as connection:
                try:
                    with connection.cursor() as cursor:
                        results = [_run_query(cursor, query) for query in queries]
                    connection.commit()
                    return [None, results]
                except Exception:
                    connection.rollback()
                    raise
        if procedure == "sql-lint":
            return [None, _lint_sql(environment, body.get("schema"), body.get("sql") or "")]
        if procedure == "query-insights":
            raise ValueError("query-insights 未实现")
        raise ValueError("未知的 procedure：" + str(procedure))
    except Exception as exc:
        return [_serialize_error(exc)]
