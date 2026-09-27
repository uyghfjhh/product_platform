#!/usr/bin/env python3
"""Raw PostgreSQL protocol driver for outstanding/cache consistency tests."""

import struct
import sys
import time

from platform_regress.clients.pgwire import (
    ProtocolClient, bind_payload, close_portal_payload, data_rows,
    describe_portal_payload, error_fields, execute_payload, message,
    parse_payload, response_summary, simple_query_message, sync_message,
)


def _parse(name, sql):
    return message("P", parse_payload(name, sql))


def _bind(portal, statement):
    return message("B", bind_payload(portal, statement))


def _describe_portal(portal):
    return message("D", describe_portal_payload(portal))


def _execute(portal):
    return message("E", execute_payload(portal))


def _sync():
    return sync_message()


def _data_rows(messages):
    return data_rows(messages)


def _response_summary(messages):
    return response_summary(messages)


def _require_error(messages, label):
    kinds, errors = _response_summary(messages)
    print("%s_RESPONSE_TYPES=%s" % (label, kinds))
    print("%s_ERROR=%s" % (label, errors[0] if errors else "<none>"))
    if not errors:
        raise RuntimeError("%s expected ErrorResponse" % label)


def _require_success(messages, label):
    kinds, errors = _response_summary(messages)
    print("%s_RESPONSE_TYPES=%s" % (label, kinds))
    if errors:
        raise RuntimeError("%s failed: %s" % (label, errors[0]))


def _require_protocol_error(messages, label, ready_status="I"):
    kinds, _ = _response_summary(messages)
    errors = [error_fields(payload) for kind, payload in messages if kind == "E"]
    sqlstates = [fields.get("C", "") for fields in errors]
    ready = [payload[:1].decode("ascii", "replace")
             for kind, payload in messages if kind == "Z" and payload]
    print("%s_RESPONSE_TYPES=%s" % (label, kinds))
    print("%s_SQLSTATE=%s" % (label, sqlstates[0] if sqlstates else "<none>"))
    print("%s_READY_STATUS=%s" % (label, ready[0] if ready else "<none>"))
    if (sqlstates != ["08P01"] or ready != [ready_status] or
            "D" in kinds):
        raise RuntimeError(
            "%s expected 08P01 and ReadyForQuery(%s), got %s %s %s" %
            (label, ready_status, kinds, sqlstates, ready))


def _prepare(client, name, sql, label):
    messages = client.parse_sync(name, sql)
    _require_success(messages, label)


def _execute_prepared(client, name, portal, expected, label):
    messages = client.execute_sync(name, portal)
    _require_success(messages, label)
    rows = _data_rows(messages)
    if expected is not None:
        actual = rows[0][0] if rows and rows[0] else None
        if actual != str(expected):
            raise RuntimeError("%s expected %s, got %s" % (label, expected, actual))
    print("%s_RESULT=%s" % (label, expected if expected is not None else "OK"))


def _pipeline(client, payload, label):
    messages = client.send(payload + _sync())
    _require_error(messages, label)
    return messages


def _backend_pid(client):
    rows, _ = client.query("SELECT pg_backend_pid()::text")
    if not rows or not rows[0]:
        raise RuntimeError("backend pid query returned no rows")
    return rows[0][0]


def _cache_phase(client, mode, phase, pid_before):
    client.query("BEGIN")
    marker = "outstanding_case_%s" % mode
    sql = (
        "SELECT pg_backend_pid()::text, name, statement "
        "FROM pg_prepared_statements "
        "WHERE statement LIKE '%" + marker + "%' ORDER BY name"
    )
    rows, _ = client.query(sql)
    pid_after = _backend_pid(client)
    print("PHASE=%s" % phase)
    print("BACKEND_PID_BEFORE=%s" % pid_before)
    print("BACKEND_PID_PHASE=%s" % pid_after)
    print("PG_CACHE_BEGIN")
    for row in rows:
        if len(row) >= 3:
            print("PG_CACHE|%s|%s" % (row[1], " ".join(row[2].split())))
    print("PG_CACHE_END")
    print("PHASE_READY=%s" % phase)
    sys.stdout.flush()
    if sys.stdin.readline() == "":
        raise RuntimeError("controller closed before phase resume")
    client.query("ROLLBACK")


def _single_failure(client, mode):
    name = "single_bad"
    bad = "SELECT * FROM outstanding_missing_single /* outstanding_case_%s_bad */" % mode
    print("CLIENT_SEQUENCE=Parse(single_bad),Sync")
    _require_error(client.parse_sync(name, bad), "PARSE_FAILURE")

    def recover():
        sql = "SELECT 11 /* outstanding_case_%s_recovered */" % mode
        _prepare(client, name, sql, "RECOVER_PARSE")
        _execute_prepared(client, name, "single_recover_portal", 11, "RECOVER_EXECUTE")
    return recover


def _shared_parse_failure(client, mode):
    sql_a = "SELECT 101 /* outstanding_case_%s_a */" % mode
    sql_b = "SELECT * FROM outstanding_missing_shared /* outstanding_case_%s_b */" % mode
    sql_c = "SELECT 103 /* outstanding_case_%s_c */" % mode
    print("CLIENT_SEQUENCE=Parse(shared_a),Parse(shared_b),Parse(shared_c),Sync")
    payload = _parse("shared_a", sql_a) + _parse("shared_b", sql_b) + _parse("shared_c", sql_c)
    _pipeline(client, payload, "SHARED_PARSE_FAILURE")

    def recover():
        _execute_prepared(client, "shared_a", "shared_a_portal", 101, "RECOVER_A")
        _prepare(client, "shared_b", "SELECT 102 /* outstanding_case_%s_b */" % mode,
                 "RECOVER_PARSE_B")
        _execute_prepared(client, "shared_b", "shared_b_portal", 102, "RECOVER_B")
        _prepare(client, "shared_c", sql_c, "RECOVER_PARSE_C")
        _execute_prepared(client, "shared_c", "shared_c_portal", 103, "RECOVER_C")
    return recover


def _execute_failure(client, mode):
    client.query("DROP TABLE IF EXISTS outstanding_exec_test")
    client.query("CREATE TABLE outstanding_exec_test(id int primary key)")
    client.query("INSERT INTO outstanding_exec_test VALUES (1)")
    sql_a = "SELECT 201 /* outstanding_case_%s_a */" % mode
    sql_b = ("INSERT INTO outstanding_exec_test VALUES (1) "
             "/* outstanding_case_%s_b */" % mode)
    sql_c = "SELECT 203 /* outstanding_case_%s_c */" % mode
    print("CLIENT_SEQUENCE=P/B/D/E(execute_a),P/B/D/E(execute_b),P/B/D/E(execute_c),Sync")
    payload = (
        _parse("execute_a", sql_a) + _bind("execute_a_portal", "execute_a") +
        _describe_portal("execute_a_portal") + _execute("execute_a_portal") +
        _parse("execute_b", sql_b) + _bind("execute_b_portal", "execute_b") +
        _describe_portal("execute_b_portal") + _execute("execute_b_portal") +
        _parse("execute_c", sql_c) + _bind("execute_c_portal", "execute_c") +
        _describe_portal("execute_c_portal") + _execute("execute_c_portal")
    )
    _pipeline(client, payload, "EXECUTE_FAILURE")

    def recover():
        _execute_prepared(client, "execute_a", "recover_execute_a", 201, "RECOVER_A")
        _prepare(client, "execute_c", sql_c, "RECOVER_PARSE_C")
        _execute_prepared(client, "execute_c", "recover_execute_c", 203, "RECOVER_C")
    return recover


def _lru_success(client, mode):
    print("CLIENT_SEQUENCE=Parse(lru_a),Sync,Parse(lru_b),Sync")
    print("EXPECTED_PROXY_SEQUENCE=Parse(lru_b),Close(global_a),Sync")
    _prepare(client, "lru_a", "SELECT 301 /* outstanding_case_%s_a */" % mode,
             "SEED_A")
    _prepare(client, "lru_b", "SELECT 302 /* outstanding_case_%s_b */" % mode,
             "PREPARE_B")

    def recover():
        _execute_prepared(client, "lru_b", "lru_success_b", 302, "RECOVER_B")
    return recover


def _lru_skipped(client, mode, multiple):
    sql_a = "SELECT 401 /* outstanding_case_%s_a */" % mode
    sql_b_bad = "SELECT * FROM outstanding_missing_lru /* outstanding_case_%s_b */" % mode
    sql_c = "SELECT 403 /* outstanding_case_%s_c */" % mode
    if multiple:
        print("CLIENT_SEQUENCE=Parse(lru_a),Sync,Parse(lru_b),Parse(lru_c),Sync")
        print("EXPECTED_PROXY_SEQUENCE=Parse(global_b),Close(global_a),Parse(global_c),Close(global_b),Sync")
    else:
        print("CLIENT_SEQUENCE=Parse(lru_a),Sync,Parse(lru_b),Sync")
        print("EXPECTED_PROXY_SEQUENCE=Parse(global_b),Close(global_a),Sync")
    _prepare(client, "lru_a", sql_a, "SEED_A")
    payload = _parse("lru_b", sql_b_bad)
    if multiple:
        payload += _parse("lru_c", sql_c)
    _pipeline(client, payload, "LRU_PARSE_FAILURE")

    def recover():
        _execute_prepared(client, "lru_a", "lru_recover_a", 401, "RECOVER_A")
        _prepare(client, "lru_b", "SELECT 402 /* outstanding_case_%s_b */" % mode,
                 "RECOVER_PARSE_B")
        _execute_prepared(client, "lru_b", "lru_recover_b", 402, "RECOVER_B")
        if multiple:
            _prepare(client, "lru_c", sql_c, "RECOVER_PARSE_C")
            _execute_prepared(client, "lru_c", "lru_recover_c", 403, "RECOVER_C")
    return recover


def _long_name(client, mode):
    name = "stmt_" + ("x" * 123)
    print("CLIENT_STATEMENT_NAME_LENGTH=%s" % len(name))
    print("CLIENT_SEQUENCE=Parse(128-byte-name),Sync")
    bad = "SELECT * FROM outstanding_missing_long /* outstanding_case_%s_bad */" % mode
    _require_error(client.parse_sync(name, bad), "LONG_NAME_PARSE_FAILURE")

    def recover():
        sql = "SELECT 501 /* outstanding_case_%s_recovered */" % mode
        _prepare(client, name, sql, "LONG_NAME_RECOVER_PARSE")
        _execute_prepared(client, name, "long_name_portal", 501,
                          "LONG_NAME_RECOVER_EXECUTE")
    return recover


def _confirmed_multiple_restore(client, mode):
    client.query("DROP TABLE IF EXISTS outstanding_lru_order_test")
    client.query("CREATE TABLE outstanding_lru_order_test(id int primary key)")
    client.query("INSERT INTO outstanding_lru_order_test VALUES (1)")
    sql_a = "SELECT 601 /* outstanding_case_%s_a */" % mode
    sql_b = ("INSERT INTO outstanding_lru_order_test VALUES (1) "
             "/* outstanding_case_%s_b */" % mode)
    sql_c = "SELECT 603 /* outstanding_case_%s_c */" % mode
    sql_d = "SELECT 604 /* outstanding_case_%s_d */" % mode
    _prepare(client, "order_a", sql_a, "SEED_A")
    _prepare(client, "order_b", sql_b, "SEED_B")
    print("CLIENT_SEQUENCE=Bind/Describe/Execute(order_b),Parse(order_c),Parse(order_d),Sync")
    print("EXPECTED_PROXY_SEQUENCE=Execute(global_b),Parse(global_c),Close(global_a),Parse(global_d),Close(global_b),Sync")
    payload = (
        _bind("order_b_error", "order_b") + _describe_portal("order_b_error") +
        _execute("order_b_error") + _parse("order_c", sql_c) +
        _parse("order_d", sql_d)
    )
    _pipeline(client, payload, "CONFIRMED_LRU_RESTORE")

    def recover():
        sql_e = "SELECT 605 /* outstanding_case_%s_e */" % mode
        _prepare(client, "order_e", sql_e, "LRU_ORDER_PARSE_E")
        _execute_prepared(client, "order_e", "order_e_portal", 605,
                          "LRU_ORDER_EXECUTE_E")
    return recover


def _fragmented_close(client, mode):
    name = "fragmented_close_statement"
    sql = "SELECT 901 /* outstanding_case_%s_seed */" % mode
    _prepare(client, name, sql, "FRAGMENTED_CLOSE_SEED")
    packet = message("C", b"S" + name.encode("utf-8") + b"\0")
    print("CLIENT_SEQUENCE=Parse,Sync,Close(each byte),Sync")
    print("FRAGMENTED_PACKET=Close(S); fragments=%s; includes header,type,name,NUL" %
          len(packet))
    sys.stdout.flush()
    messages = client.send_fragmented(b"", packet)
    _require_success(messages, "FRAGMENTED_CLOSE")
    kinds, _ = _response_summary(messages)
    if "3" not in kinds or "Z" not in kinds:
        raise RuntimeError("fragmented Close expected CloseComplete and ReadyForQuery: %s" % kinds)

    def recover():
        recovered = "SELECT 902 /* outstanding_case_%s_recovered */" % mode
        _prepare(client, name, recovered, "CLOSE_RECOVER_PARSE")
        _execute_prepared(client, name, "close_recover_portal", 902,
                          "CLOSE_RECOVER_EXECUTE")
    return recover


def _fragmented_execute(client, mode):
    name = "fragmented_execute_statement"
    portal = "fragmented_execute_portal"
    sql = "SELECT 911 /* outstanding_case_%s_seed */" % mode
    _prepare(client, name, sql, "FRAGMENTED_EXECUTE_SEED")
    bind = _bind(portal, name) + _describe_portal(portal)
    execute = _execute(portal)
    print("CLIENT_SEQUENCE=Parse,Sync,Bind,Describe,Execute(each byte),Sync")
    print("FRAGMENTED_PACKET=Execute; fragments=%s; includes header,portal,NUL,max_rows" %
          len(execute))
    sys.stdout.flush()
    close_portal = message("C", close_portal_payload(portal))
    messages = client.send_fragmented(bind, execute, close_portal)
    _require_success(messages, "FRAGMENTED_EXECUTE")
    rows = _data_rows(messages)
    actual = rows[-1][0] if rows and rows[-1] else None
    if actual != "911":
        raise RuntimeError("fragmented Execute expected 911, got %s" % actual)
    print("FRAGMENTED_EXECUTE_RESULT=911")

    def recover():
        _execute_prepared(client, name, "execute_recover_portal", 911,
                          "EXECUTE_RECOVER")
    return recover


def _execute_payload_validation(client, mode):
    name = "execute_validation_statement"
    sql = "SELECT 921 /* outstanding_case_%s_seed */" % mode
    _prepare(client, name, sql, "EXECUTE_VALIDATION_SEED")
    print("CLIENT_SEQUENCE=malformed Execute + Sync, then legal Execute + Sync")

    portal_missing = "execute_missing_max_rows"
    missing_max_rows = message("E", portal_missing.encode("utf-8") + b"\0")
    messages = client.send(_bind(portal_missing, name) + missing_max_rows + _sync())
    _require_protocol_error(messages, "EXECUTE_MISSING_MAX_ROWS")

    portal_trailing = "execute_trailing_data"
    trailing_data = message(
        "E", portal_trailing.encode("utf-8") + b"\0" +
        struct.pack("!I", 0) + b"x")
    messages = client.send(_bind(portal_trailing, name) + trailing_data + _sync())
    _require_protocol_error(messages, "EXECUTE_TRAILING_DATA")

    portal_zero = "execute_max_rows_zero"
    messages = client.send(
        _bind(portal_zero, name) + _describe_portal(portal_zero) +
        _execute(portal_zero) +
        message("C", close_portal_payload(portal_zero)) +
        _sync())
    _require_success(messages, "EXECUTE_MAX_ROWS_ZERO")
    rows = _data_rows(messages)
    if not rows or rows[-1][0] != "921":
        raise RuntimeError("max_rows=0 Execute did not return 921")

    portal_one = "execute_max_rows_one"
    execute_one = message(
        "E", portal_one.encode("utf-8") + b"\0" + struct.pack("!I", 1))
    messages = client.send(
        _bind(portal_one, name) + _describe_portal(portal_one) +
        execute_one +
        message("C", close_portal_payload(portal_one)) +
        _sync())
    _require_success(messages, "EXECUTE_MAX_ROWS_ONE")
    rows = _data_rows(messages)
    if not rows or rows[-1][0] != "921":
        raise RuntimeError("max_rows=1 Execute did not return 921")

    client.query("BEGIN")
    portal_tx = "execute_invalid_in_transaction"
    missing_in_tx = message("E", portal_tx.encode("utf-8") + b"\0")
    messages = client.send(_bind(portal_tx, name) + missing_in_tx + _sync())
    _require_protocol_error(messages, "EXECUTE_INVALID_IN_TRANSACTION", "E")
    client.query("ROLLBACK")
    print("EXECUTE_INVALID_TRANSACTION_ROLLBACK=OK")

    def recover():
        _execute_prepared(client, name, "execute_validation_recovery", 921,
                          "EXECUTE_VALIDATION_RECOVERY")
    return recover


def _connect_with_retry(port, database, attempts=30):
    last_error = None
    for _ in range(attempts):
        try:
            return ProtocolClient("127.0.0.1", port, database)
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError("failed to reconnect after backend isolation: %s" % last_error)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: outstanding_protocol_probe.py PORT MODE DATABASE")
    mode = sys.argv[2]
    port = sys.argv[1]
    database = sys.argv[3]
    client = _connect_with_retry(port, database)
    try:
        pid_before = _backend_pid(client)
        print("CLIENT_PROTOCOL=PostgreSQL raw protocol; cache inspection uses Simple Query Q")
        if mode == "parse_failure_single":
            recover = _single_failure(client, mode)
        elif mode == "parse_failure_shared_sync":
            recover = _shared_parse_failure(client, mode)
        elif mode == "execute_failure_shared_sync":
            recover = _execute_failure(client, mode)
        elif mode == "lru_close_success":
            recover = _lru_success(client, mode)
        elif mode == "lru_close_skipped_restore":
            recover = _lru_skipped(client, mode, False)
        elif mode == "lru_close_multiple_restore":
            recover = _lru_skipped(client, mode, True)
        elif mode == "lru_confirmed_multiple_restore_order":
            recover = _confirmed_multiple_restore(client, mode)
        elif mode == "long_statement_name_cleanup":
            recover = _long_name(client, mode)
        elif mode == "fragmented_close_packet":
            recover = _fragmented_close(client, mode)
        elif mode == "fragmented_execute_packet":
            recover = _fragmented_execute(client, mode)
        elif mode == "execute_payload_validation":
            recover = _execute_payload_validation(client, mode)
        else:
            raise RuntimeError("unknown mode %s" % mode)

        _cache_phase(client, mode, "AFTER_ERROR", pid_before)
        recover()
        print("RECOVERY=OK")
        _cache_phase(client, mode, "AFTER_RECOVERY", pid_before)
        print("OUTSTANDING_TEST=OK")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
