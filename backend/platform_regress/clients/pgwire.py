"""Minimal PostgreSQL frontend/backend wire-protocol client.

Used by protocol-level regression probes that must send raw extended-query
messages (Parse/Bind/Describe/Execute/Sync) and assert on individual backend
message kinds, SQLSTATEs, CommandComplete tags, and ReadyForQuery status —
behavior libpq/psql intentionally hides.

The module deliberately exposes both layers:

- frame helpers (:func:`message`, :func:`startup_payload`,
  :func:`send_message`, :func:`read_message`, :func:`read_responses`) for
  probes that craft malformed or unusual sequences (truncated Bind, binary
  result formats, pipelined writes);
- :func:`extended_execute` for the common one-cycle-per-statement shape that
  records every received message kind plus decoded SQLSTATE/CommandComplete/
  first-column/transaction-status fields.
"""

import socket
import struct

_PROTOCOL_VERSION = 196608  # 3.0
_MAX_MESSAGE_LENGTH = 16 * 1024 * 1024


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
    if length < 4 or length > _MAX_MESSAGE_LENGTH:
        raise RuntimeError("invalid backend message length %d" % length)
    return kind, read_exact(sock, length - 4)


def send_message(sock, kind, payload=b""):
    if isinstance(kind, str):
        kind = kind.encode("ascii")
    sock.sendall(kind + struct.pack("!I", len(payload) + 4) + payload)


def message(kind, payload):
    if isinstance(kind, str):
        kind = kind.encode("ascii")
    return kind + struct.pack("!I", len(payload) + 4) + payload


def startup_payload(database, user, application_name=None):
    payload = struct.pack("!I", _PROTOCOL_VERSION)
    pairs = [("user", user), ("database", database)]
    if application_name:
        pairs.append(("application_name", application_name))
    for key, value in pairs:
        payload += key.encode() + b"\0" + str(value).encode() + b"\0"
    payload += b"\0"
    return struct.pack("!I", len(payload) + 4) + payload


def error_fields(payload):
    fields = {}
    for field in payload.split(b"\0"):
        if len(field) >= 2:
            fields[field[:1].decode("ascii", "replace")] = field[1:].decode(
                "utf-8", "replace")
    return fields


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


def read_messages_until_ready(sock):
    messages = []
    while True:
        kind, payload = read_message(sock)
        messages.append((kind, payload))
        if kind == "Z":
            return messages


def read_responses(sock, stop_on_ready=True):
    """Collect backend message kinds until ReadyForQuery or EOF.

    Unlike :func:`read_messages_until_ready`, a peer that closes the socket
    mid-response yields the partial kind list instead of raising — probes
    asserting on rejection semantics need to see ``["E"]`` rather than a
    transport exception.
    """
    kinds = []
    while True:
        header = b""
        while len(header) < 5:
            chunk = sock.recv(5 - len(header))
            if not chunk:
                return kinds
            header += chunk
        kind = header[:1].decode("ascii", "replace")
        length = struct.unpack("!I", header[1:])[0]
        read_exact(sock, length - 4)
        kinds.append(kind)
        if stop_on_ready and kind == "Z":
            return kinds


def parse_payload(name, sql):
    return (name.encode("utf-8") + b"\0" + sql.encode("utf-8") + b"\0" +
            struct.pack("!H", 0))


def bind_payload(portal, statement):
    return (portal.encode("utf-8") + b"\0" + statement.encode("utf-8") +
            b"\0" + struct.pack("!HHH", 0, 0, 0))


def describe_portal_payload(portal):
    return b"P" + portal.encode("utf-8") + b"\0"


def execute_payload(portal):
    return portal.encode("utf-8") + b"\0" + struct.pack("!I", 0)


def close_portal_payload(portal):
    return b"P" + portal.encode("utf-8") + b"\0"


def sync_message():
    return message("S", b"")


def simple_query_message(sql):
    return message("Q", sql.encode("utf-8") + b"\0")


def terminate_message():
    return message("X", b"")


def data_rows(messages):
    rows = []
    for kind, payload in messages:
        if kind != "D":
            continue
        if len(payload) < 2:
            raise RuntimeError("truncated DataRow")
        count = struct.unpack("!H", payload[:2])[0]
        offset = 2
        row = []
        for _ in range(count):
            if offset + 4 > len(payload):
                raise RuntimeError("truncated DataRow length")
            value_len = struct.unpack("!i", payload[offset:offset + 4])[0]
            offset += 4
            if value_len < 0:
                row.append(None)
                continue
            if offset + value_len > len(payload):
                raise RuntimeError("truncated DataRow value")
            row.append(payload[offset:offset + value_len].decode(
                "utf-8", "replace"))
            offset += value_len
        rows.append(row)
    return rows


def response_summary(messages):
    kinds = "".join(kind for kind, _ in messages)
    errors = [error_fields(payload).get("M", "unknown backend error")
              for kind, payload in messages if kind == "E"]
    return kinds, errors


def connect(host, port, database, user, application_name=None, timeout=10):
    sock = socket.create_connection((host, int(port)), timeout)
    sock.sendall(startup_payload(database, user, application_name))
    while True:
        kind, body = read_message(sock)
        if kind == "R":
            auth_type = struct.unpack("!I", body[:4])[0]
            if auth_type != 0:
                sock.close()
                raise RuntimeError("unsupported authentication type %d" % auth_type)
        elif kind == "E":
            fields = error_fields(body)
            sock.close()
            raise RuntimeError("startup failed: %s" % fields)
        elif kind == "Z":
            if body != b"I":
                sock.close()
                raise RuntimeError(
                    "unexpected startup transaction status %r" % body)
            return sock


def extended_execute(sock, sql, variant=None, step=None, describe_kind=b"P"):
    """Send one P/B/D/E/S extended cycle for ``sql`` and record the response.

    Returns a dict shaped for protocol assertions: ``received`` lists every
    backend message kind in order; ``sqlstate``/``error`` come from
    ErrorResponse; ``command_tag`` from CommandComplete; ``value`` is the
    first column of the first DataRow (``None`` when absent or NULL);
    ``ready`` is the transaction status byte of ReadyForQuery.
    """
    name = b"\0"
    parse = name + sql.encode("utf-8") + b"\0" + struct.pack("!H", 0)
    bind = name + name + struct.pack("!HHH", 0, 0, 0)
    describe = describe_kind + name
    execute_body = name + struct.pack("!I", 0)
    for kind, payload in (("P", parse), ("B", bind), ("D", describe),
                          ("E", execute_body), ("S", b"")):
        send_message(sock, kind, payload)
    record = {"variant": variant, "step": step, "sql": sql,
              "sent": "P/B/D/E/S", "received": [], "sqlstate": None,
              "ready": None, "value": None, "command_tag": None}
    while True:
        kind, body = read_message(sock)
        record["received"].append(kind)
        if kind == "E":
            fields = error_fields(body)
            record["sqlstate"] = fields.get("C")
            record["error"] = fields.get("M")
        elif kind == "D":
            record["value"] = first_column(body)
        elif kind == "C":
            record["command_tag"] = body.rstrip(b"\0").decode("utf-8", "replace")
        elif kind == "Z":
            record["ready"] = body.decode("ascii")
            return record


class ProtocolClient:
    """Stateful protocol session: startup plus arbitrary message pipelines.

    Keeps a single socket open so probes can interleave named statements,
    portals, malformed sequences and simple queries against one backend —
    which is what proxy/routing regressions actually exercise.
    """

    def __init__(self, host, port, database, user="postgres",
                 application_name=None, timeout=10):
        self.sock = socket.create_connection((host, int(port)), timeout)
        self.sock.settimeout(30)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            self.sock.sendall(startup_payload(database, user, application_name))
            messages = read_messages_until_ready(self.sock)
            if not messages or messages[-1][0] != "Z":
                raise RuntimeError("startup did not reach ReadyForQuery")
        except Exception:
            self.sock.close()
            raise

    def close(self):
        try:
            self.sock.sendall(terminate_message())
        except OSError:
            pass
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, payload):
        self.sock.sendall(payload)
        return read_messages_until_ready(self.sock)

    def query(self, sql):
        messages = self.send(simple_query_message(sql))
        errors = [error_fields(payload).get("M", "unknown backend error")
                  for kind, payload in messages if kind == "E"]
        if errors:
            raise RuntimeError("simple query failed: %s" % errors[0])
        return data_rows(messages), messages

    def send_fragmented(self, prefix, packet, suffix=b"", delay=0.01):
        """Byte-at-a-time writes for segmentation/timeout torture cases."""
        import time
        if prefix:
            self.sock.sendall(prefix)
        for byte in packet:
            self.sock.sendall(bytes((byte,)))
            time.sleep(delay)
        self.sock.sendall(suffix + sync_message())
        return read_messages_until_ready(self.sock)

    def parse_sync(self, name, sql):
        return self.send(message("P", parse_payload(name, sql)) + sync_message())

    def execute_sync(self, statement, portal):
        return self.send(
            message("B", bind_payload(portal, statement)) +
            message("D", describe_portal_payload(portal)) +
            message("E", execute_payload(portal)) +
            message("C", close_portal_payload(portal)) +
            sync_message()
        )
