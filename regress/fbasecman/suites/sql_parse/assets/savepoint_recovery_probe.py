#!/usr/bin/env python3
"""Send one Extended Query cycle per SQL and record every server response."""

import json
import socket
import struct
import sys


def read_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise RuntimeError("connection closed while reading %d bytes" % length)
        data.extend(chunk)
    return bytes(data)


def read_message(sock):
    kind = read_exact(sock, 1).decode("ascii")
    length = struct.unpack("!I", read_exact(sock, 4))[0]
    if length < 4 or length > 16 * 1024 * 1024:
        raise RuntimeError("invalid backend message length %d" % length)
    return kind, read_exact(sock, length - 4)


def send_message(sock, kind, payload=b""):
    sock.sendall(kind.encode("ascii") + struct.pack("!I", len(payload) + 4) + payload)


def error_fields(payload):
    fields = {}
    for field in payload.split(b"\0"):
        if len(field) >= 2:
            fields[field[:1].decode("ascii", "replace")] = field[1:].decode("utf-8", "replace")
    return fields


def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    payload = struct.pack("!I", 196608) + b"user\0postgres\0database\0mmr_group\0\0"
    sock.sendall(struct.pack("!I", len(payload) + 4) + payload)
    while True:
        kind, body = read_message(sock)
        if kind == "R":
            auth_type = struct.unpack("!I", body[:4])[0]
            if auth_type != 0:
                raise RuntimeError("unsupported authentication type %d" % auth_type)
        elif kind == "E":
            raise RuntimeError("startup failed: %s" % error_fields(body))
        elif kind == "Z":
            if body != b"I":
                raise RuntimeError("unexpected startup transaction status %r" % body)
            return sock


def first_column(payload):
    if len(payload) < 6:
        raise RuntimeError("truncated DataRow")
    count = struct.unpack("!H", payload[:2])[0]
    if count < 1:
        return None
    length = struct.unpack("!i", payload[2:6])[0]
    if length < 0:
        return None
    if len(payload) < 6 + length:
        raise RuntimeError("truncated DataRow field")
    return payload[6:6 + length].decode("utf-8", "replace")


def execute(sock, variant, label, sql):
    name = b"\0"
    parse = name + sql.encode("utf-8") + b"\0" + struct.pack("!H", 0)
    bind = name + name + struct.pack("!HHH", 0, 0, 0)
    describe = b"P" + name
    execute_body = name + struct.pack("!I", 0)
    for kind, payload in (("P", parse), ("B", bind), ("D", describe),
                          ("E", execute_body), ("S", b"")):
        send_message(sock, kind, payload)
    result = {"variant": variant, "step": label, "sql": sql,
              "sent": "P/B/D/E/S", "received": [], "sqlstate": None,
              "ready": None, "value": None, "command_tag": None}
    while True:
        kind, body = read_message(sock)
        result["received"].append(kind)
        if kind == "E":
            fields = error_fields(body)
            result["sqlstate"] = fields.get("C")
            result["error"] = fields.get("M")
        elif kind == "D":
            result["value"] = first_column(body)
        elif kind == "C":
            result["command_tag"] = body.rstrip(b"\0").decode("utf-8", "replace")
        elif kind == "Z":
            result["ready"] = body.decode("ascii")
            print("STEP_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)
            return


def run_variant(port, variant, extra_failure):
    with connect(port) as sock:
        steps = (("begin", "BEGIN"), ("savepoint", "SAVEPOINT s4"),
                 ("division", "SELECT 1/0"))
        for label, sql in steps:
            execute(sock, variant, label, sql)
        if extra_failure:
            execute(sock, variant, "aborted_select", "SELECT 1")
        execute(sock, variant, "rollback_to", "ROLLBACK TO SAVEPOINT s4")
        execute(sock, variant, "recovery_select", "SELECT 9")
        execute(sock, variant, "cleanup", "ROLLBACK")


if __name__ == "__main__":
    port = int(sys.argv[1])
    run_variant(port, "direct_recovery", False)
    run_variant(port, "after_local_25p02", True)
