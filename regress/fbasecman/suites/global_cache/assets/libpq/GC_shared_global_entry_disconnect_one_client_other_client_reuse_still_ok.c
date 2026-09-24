#include <libpq-fe.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fail(PGconn *conn, const char *stage) {
    fprintf(stderr, "%s failed: %s\n", stage, conn ? PQerrorMessage(conn) : "unknown");
    if (conn) {
        PQfinish(conn);
    }
    exit(1);
}

static void drain(PGconn *conn) {
    PGresult *res;
    while ((res = PQgetResult(conn)) != NULL) {
        PQclear(res);
    }
}

static void exec_prepared_ok(PGconn *conn, const char *stmt_name, const char *tag) {
    const char *params[1] = {"1"};
    PGresult *res = PQexecPrepared(conn, stmt_name, 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        fail(conn, tag);
    }
    printf("%s rows=%d", tag, PQntuples(res));
    if (PQntuples(res) > 0)
        printf(" value=%s", PQgetvalue(res, 0, 0));
    printf("\n");
    fflush(stdout);
    PQclear(res);
    drain(conn);
}

int main(int argc, char **argv) {
    const char *sql = "select name from test where id = $1 /* gc_shared_disconnect_reuse */";
    Oid types[1] = {23};
    PGconn *conn1 = NULL;
    PGconn *conn2 = NULL;
    PGresult *res = NULL;
    char cmd[64];

    if (argc < 2) {
        return 1;
    }

    conn1 = PQconnectdb(argv[1]);
    if (PQstatus(conn1) != CONNECTION_OK) {
        fail(conn1, "conn1 connect");
    }
    conn2 = PQconnectdb(argv[1]);
    if (PQstatus(conn2) != CONNECTION_OK) {
        fail(conn2, "conn2 connect");
    }

    res = PQprepare(conn1, "stmt1", sql, 1, types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        fail(conn1, "conn1 prepare");
    }
    PQclear(res);
    drain(conn1);
    exec_prepared_ok(conn1, "stmt1", "conn1_first_ok");

    res = PQprepare(conn2, "stmt2", sql, 1, types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        fail(conn2, "conn2 prepare");
    }
    PQclear(res);
    drain(conn2);
    exec_prepared_ok(conn2, "stmt2", "conn2_first_ok");

    printf("READY_BOTH_CONNECTED\n");
    fflush(stdout);

    while (fgets(cmd, sizeof(cmd), stdin) != NULL) {
        if (strncmp(cmd, "disconnect1", 11) == 0) {
            PQfinish(conn1);
            conn1 = NULL;
            printf("READY_AFTER_DISCONNECT1\n");
            fflush(stdout);
        } else if (strncmp(cmd, "reuse2", 6) == 0) {
            exec_prepared_ok(conn2, "stmt2", "conn2_second_ok");
            printf("READY_AFTER_REUSE2\n");
            fflush(stdout);
        } else if (strncmp(cmd, "disconnect2", 11) == 0) {
            PQfinish(conn2);
            conn2 = NULL;
            printf("READY_AFTER_DISCONNECT2\n");
            fflush(stdout);
            break;
        } else if (strncmp(cmd, "exit", 4) == 0) {
            break;
        }
    }

    if (conn1) {
        PQfinish(conn1);
    }
    if (conn2) {
        PQfinish(conn2);
    }
    return 0;
}
