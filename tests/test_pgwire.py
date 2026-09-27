import socket
import struct
import threading

import pytest

from platform_regress.clients.pgwire import (
    ProtocolClient, bind_payload, close_portal_payload, connect, data_rows,
    describe_portal_payload, error_fields, execute_payload, extended_execute,
    first_column, message, parse_payload, read_message,
    read_messages_until_ready, read_responses, response_summary, send_message,
    simple_query_message, startup_payload, sync_message, terminate_message,
)


def test_message_frames_kind_and_length():
    assert message("S", b"") == b"S" + struct.pack("!I", 4)
    frame = message("P", b"ab")
    assert frame[:1] == b"P" and struct.unpack("!I", frame[1:5])[0] == 6
    assert frame[5:] == b"ab"


def test_startup_payload_version_and_parameters():
    payload = startup_payload("db", "u", "app")
    assert struct.unpack("!I", payload[:4])[0] == len(payload)
    assert struct.unpack("!I", payload[4:8])[0] == 196608
    assert b"user\0u\0" in payload and b"database\0db\0" in payload
    assert b"application_name\0app\0" in payload


def test_error_fields_parses_null_separated_codes():
    fields = error_fields(b"Sseverity\0C28000\0Mbad credentials\0\0")
    assert fields == {"S": "severity", "C": "28000", "M": "bad credentials"}


def test_first_column_decodes_value_and_null():
    payload = struct.pack("!H", 1) + struct.pack("!i", 3) + b"abc"
    assert first_column(payload) == "abc"
    null_payload = struct.pack("!H", 1) + struct.pack("!i", -1)
    assert first_column(null_payload) is None
    with pytest.raises(RuntimeError, match="truncated DataRow"):
        first_column(struct.pack("!H", 0))


def test_payload_builders():
    assert parse_payload("s", "SELECT 1").startswith(b"s\0SELECT 1\0")
    assert bind_payload("p", "s").startswith(b"p\0s\0")
    assert describe_portal_payload("p") == b"Pp\0"
    assert execute_payload("p") == b"p\0" + struct.pack("!I", 0)
    assert close_portal_payload("p") == b"Pp\0"
    assert sync_message() == b"S" + struct.pack("!I", 4)
    assert simple_query_message("SELECT 1") == b"Q" + struct.pack("!I", 13) + b"SELECT 1\0"
    assert terminate_message() == b"X" + struct.pack("!I", 4)


def _backend(sock, script):
    """Feed a scripted byte stream; capture what the client sends."""
    received = []

    def run():
        sock.sendall(script)
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                received.append(data)
        except OSError:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return received


def _ready(status=b"I"):
    return message("Z", status)


def test_connect_completes_startup(monkeypatch):
    client, server = socket.socketpair()
    _backend(server, message("R", struct.pack("!I", 0)) + _ready())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: client)
    assert connect("127.0.0.1", 1, "db", "u") is client
    client.close()
    server.close()


def test_connect_rejects_unsupported_auth(monkeypatch):
    client, server = socket.socketpair()
    _backend(server, message("R", struct.pack("!I", 5)) + _ready())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: client)
    with pytest.raises(RuntimeError, match="authentication type 5"):
        connect("127.0.0.1", 1, "db", "u")
    server.close()


def test_read_message_and_until_ready():
    client, server = socket.socketpair()
    server.sendall(message("T", b"desc") + _ready(b"I"))
    kind, payload = read_message(client)
    assert kind == "T" and payload == b"desc"
    messages = read_messages_until_ready(client)
    assert messages == [("Z", b"I")]
    client.close()
    server.close()


def test_read_responses_tolerates_close_without_ready():
    client, server = socket.socketpair()
    server.sendall(message("E", b"Serr\0\0"))
    server.close()
    assert read_responses(client) == ["E"]
    client.close()


def test_extended_execute_records_response_kinds():
    client, server = socket.socketpair()
    row = message("D", struct.pack("!H", 1) + struct.pack("!i", 1) + b"9")
    complete = message("C", b"SELECT 1\0")
    _backend(server, message("1", b"") + message("2", b"") + message("n", b"") +
             row + complete + _ready(b"I"))
    record = extended_execute(client, "SELECT 9", variant="v", step="s")
    assert record["sent"] == "P/B/D/E/S"
    assert record["received"] == ["1", "2", "n", "D", "C", "Z"]
    assert record["value"] == "9"
    assert record["command_tag"] == "SELECT 1"
    assert record["ready"] == "I" and record["sqlstate"] is None
    client.close()
    server.close()


def test_extended_execute_captures_sqlstate():
    client, server = socket.socketpair()
    err = message("E", b"SERROR\0C22012\0Mdivision by zero\0\0")
    _backend(server, err + _ready(b"E"))
    record = extended_execute(client, "SELECT 1/0")
    assert record["sqlstate"] == "22012"
    assert record["error"] == "division by zero"
    assert record["ready"] == "E"
    client.close()
    server.close()


def test_data_rows_decodes_columns_and_nulls():
    payload = (struct.pack("!H", 2) + struct.pack("!i", 3) + b"abc" +
               struct.pack("!i", -1))
    assert data_rows([("D", payload), ("Z", b"I")]) == [["abc", None]]


def test_response_summary_joins_kinds_and_errors():
    kinds, errors = response_summary(
        [("E", b"Mbad\0\0"), ("Z", b"I")])
    assert kinds == "EZ" and errors == ["bad"]


def test_protocol_client_simple_query_and_context_manager(monkeypatch):
    client, server = socket.socketpair()
    startup = message("R", struct.pack("!I", 0)) + _ready()
    row = message("D", struct.pack("!H", 1) + struct.pack("!i", 1) + b"7")
    _backend(server, startup + message("T", b"") + row +
             message("C", b"SELECT 1\0") + _ready())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: client)
    with ProtocolClient("127.0.0.1", 1, "db") as session:
        assert session.sock is client
        rows, messages = session.query("SELECT 7")
        assert rows == [["7"]]
        assert messages[-1] == ("Z", b"I")
    server.close()


def test_send_message_accepts_str_and_bytes():
    client, server = socket.socketpair()
    send_message(client, "S")
    send_message(client, b"S")
    assert read_message(server) == ("S", b"")
    assert read_message(server) == ("S", b"")
    client.close()
    server.close()
