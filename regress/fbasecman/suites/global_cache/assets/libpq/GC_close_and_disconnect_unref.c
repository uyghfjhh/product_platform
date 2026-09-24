#include <arpa/inet.h>
#include <errno.h>
#include <libpq-fe.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static void fail(PGconn *conn, const char *stage)
{
    fprintf(stderr, "%s failed: %s\n", stage, conn ? PQerrorMessage(conn) : "");
    if (conn) {
        PQfinish(conn);
    }
    exit(1);
}

static void drain(PGconn *conn)
{
    PGresult *res;
    while ((res = PQgetResult(conn)) != NULL) {
        PQclear(res);
    }
}

static int write_all(int sock, const unsigned char *buf, size_t len)
{
    while (len > 0) {
        ssize_t n = send(sock, buf, len, 0);
        if (n <= 0)
            return -1;
        buf += n;
        len -= (size_t)n;
    }
    return 0;
}

static int read_all(int sock, unsigned char *buf, size_t len)
{
    while (len > 0) {
        ssize_t n = recv(sock, buf, len, 0);
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                struct pollfd pfd;
                pfd.fd = sock;
                pfd.events = POLLIN;
                pfd.revents = 0;
                if (poll(&pfd, 1, 3000) > 0)
                    continue;
            }
            return -1;
        }
        buf += n;
        len -= (size_t)n;
    }
    return 0;
}

static void send_close_named_statement(PGconn *conn, const char *stmt_name)
{
    int sock = PQsocket(conn);
    uint32_t name_len = (uint32_t)strlen(stmt_name) + 1;
    uint32_t body_len = 4 + 1 + name_len;
    uint32_t net_len = htonl(body_len);
    size_t packet_len = 1 + body_len;
    unsigned char *packet = (unsigned char *)malloc(packet_len);
    unsigned char header[5];
    unsigned char *body = NULL;
    uint32_t response_len = 0;
    char msg_type = 0;

    if (packet == NULL) {
        fprintf(stderr, "malloc Close packet failed\n");
        exit(1);
    }

    packet[0] = 'C';
    memcpy(packet + 1, &net_len, 4);
    packet[5] = 'S';
    memcpy(packet + 6, stmt_name, name_len);

    if (write_all(sock, packet, packet_len) != 0) {
        free(packet);
        fprintf(stderr, "send Close packet failed\n");
        exit(1);
    }
    free(packet);

    if (read_all(sock, header, sizeof(header)) != 0) {
        fprintf(stderr, "recv CloseComplete header failed\n");
        exit(1);
    }
    msg_type = (char)header[0];
    memcpy(&response_len, header + 1, 4);
    response_len = ntohl(response_len);
    if (response_len < 4) {
        fprintf(stderr, "invalid CloseComplete length: %u\n", response_len);
        exit(1);
    }
    if (response_len > 4) {
        body = (unsigned char *)malloc(response_len - 4);
        if (body == NULL) {
            fprintf(stderr, "malloc CloseComplete body failed\n");
            exit(1);
        }
        if (read_all(sock, body, response_len - 4) != 0) {
            free(body);
            fprintf(stderr, "recv CloseComplete body failed\n");
            exit(1);
        }
        free(body);
    }
    if (msg_type != '3') {
        fprintf(stderr, "expected CloseComplete('3'), got '%c'\n", msg_type);
        exit(1);
    }
}

static void wait_stdin_line(void)
{
    char buf[64];
    if (fgets(buf, sizeof(buf), stdin) == NULL) {
        fprintf(stderr, "stdin command missing\n");
        exit(1);
    }
}

static void close_path(const char *conninfo)
{
    PGconn *conn;
    PGresult *res;
    const char *params[1] = {"1"};
    Oid param_types[1] = {23};

    conn = PQconnectdb(conninfo);
    if (PQstatus(conn) != CONNECTION_OK)
        fail(conn, "PQconnectdb close_path");

    res = PQprepare(conn, "stmt_close_unref",
                    "select name from test where id = $1 /* gc_close_unref_close */",
                    1, param_types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        fail(conn, "PQprepare close_path");
    }
    PQclear(res);
    drain(conn);

    res = PQexecPrepared(conn, "stmt_close_unref", 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        fail(conn, "PQexecPrepared close_path");
    }
    if (PQntuples(res) > 0)
        printf("close_path=%s\n", PQgetvalue(res, 0, 0));
    PQclear(res);
    drain(conn);

    printf("READY_CLOSE_BEFORE\n");
    fflush(stdout);
    wait_stdin_line();
    send_close_named_statement(conn, "stmt_close_unref");
    printf("READY_CLOSE_AFTER\n");
    fflush(stdout);
    wait_stdin_line();
    PQfinish(conn);
}

static void disconnect_path(const char *conninfo)
{
    PGconn *conn;
    PGresult *res;
    const char *params[1] = {"2"};
    Oid param_types[1] = {23};

    conn = PQconnectdb(conninfo);
    if (PQstatus(conn) != CONNECTION_OK)
        fail(conn, "PQconnectdb disconnect_path");

    res = PQprepare(conn, "stmt_disconnect_unref",
                    "select name from test where id = $1 /* gc_close_unref_disconnect */",
                    1, param_types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        fail(conn, "PQprepare disconnect_path");
    }
    PQclear(res);
    drain(conn);

    res = PQexecPrepared(conn, "stmt_disconnect_unref", 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        fail(conn, "PQexecPrepared disconnect_path");
    }
    if (PQntuples(res) > 0)
        printf("disconnect_path=%s\n", PQgetvalue(res, 0, 0));
    PQclear(res);
    drain(conn);

    printf("READY_DISCONNECT_BEFORE\n");
    fflush(stdout);
    wait_stdin_line();
    PQfinish(conn);
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <conninfo> <close|disconnect>\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[2], "close") == 0) {
        close_path(argv[1]);
        return 0;
    }
    if (strcmp(argv[2], "disconnect") == 0) {
        disconnect_path(argv[1]);
        return 0;
    }
    fprintf(stderr, "unknown mode: %s\n", argv[2]);
    return 1;
}
