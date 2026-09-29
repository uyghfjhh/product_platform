#!/usr/bin/env python3
"""Minimal PostgreSQL extended-protocol probe for SQL_PARSE heartbeat Bind."""

import os
import struct
import sys

from platform_regress.clients.pgwire import connect, message, read_responses


def parse(sock):
    payload = b"hb\0SELECT 1\0" + struct.pack("!H", 0)
    sock.sendall(message("P", payload) + message("S", b""))
    return read_responses(sock)


def bind_execute(sock, mode="normal"):
    payload = b"\0hb\0" + struct.pack("!H", 0) + struct.pack("!H", 0)
    if mode == "normal":
        payload += struct.pack("!H", 0)
    elif mode == "binary":
        payload += struct.pack("!H", 1) + struct.pack("!H", 1)
    sock.sendall(message("B", payload) + message("E", b"\0" + struct.pack("!I", 0)) + message("S", b""))
    return read_responses(sock)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: heartbeat_bind_probe.py PORT normal|malformed")
    sock = connect(os.environ.get("FBCMAN_LOCAL_HOST", "127.0.0.1"), sys.argv[1], "mmr_group", "postgres",
                   application_name="ha_heartbeat_bind_probe", timeout=5)
    try:
        parse_messages = parse(sock)
        bind_messages = bind_execute(sock, sys.argv[2])
        print("CLIENT_SEQUENCE=Startup,Parse,Sync,Bind,Execute,Sync")
        print("CLIENT_BIND_FIELDS=portal,statement,param_format_count,param_count,result_format_count%s" %
              ("(TRUNCATED)" if sys.argv[2] == "malformed" else
               "(binary result)" if sys.argv[2] == "binary" else ""))
        print("PARSE_MESSAGES=%s" % ",".join(parse_messages))
        print("BIND_MESSAGES=%s" % ",".join(bind_messages))
        if sys.argv[2] == "normal":
            ok = all(item in bind_messages for item in ("2", "D", "C", "Z"))
            print("HEARTBEAT_LOCAL_BIND=%s" % ("OK" if ok else "FAIL"))
            return 0 if ok else 1
        if sys.argv[2] == "malformed":
            ok = "E" in bind_messages and "Z" in bind_messages and "D" not in bind_messages
            print("HEARTBEAT_INVALID_BIND_REJECTED=%s" % ("OK" if ok else "FAIL"))
        else:
            ok = "2" in bind_messages and "D" in bind_messages and "C" in bind_messages and "Z" in bind_messages
            print("HEARTBEAT_UNSUPPORTED_FORMAT_FALLBACK=%s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main())
