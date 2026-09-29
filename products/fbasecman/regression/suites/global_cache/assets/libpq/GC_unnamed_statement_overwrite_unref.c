#include <libpq-fe.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void finish_with_error(PGconn *conn, const char *stage)
{
    fprintf(stderr, "%s failed: %s\n", stage, PQerrorMessage(conn));
    PQfinish(conn);
    exit(1);
}

static void drain_results(PGconn *conn)
{
    PGresult *res;
    while ((res = PQgetResult(conn)) != NULL) {
        PQclear(res);
    }
}

static void exec_simple(PGconn *conn, const char *sql)
{
    PGresult *res = PQexec(conn, sql);
    if (PQresultStatus(res) != PGRES_COMMAND_OK && PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        finish_with_error(conn, sql);
    }
    PQclear(res);
    drain_results(conn);
}

static void run_unnamed(PGconn *conn, const char *sql, const char *param, const char *tag)
{
    PGresult *res;
    const char *param_values[1];
    Oid param_types[1];

    param_values[0] = param;
    param_types[0] = 23;

    res = PQprepare(conn, "", sql, 1, param_types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        finish_with_error(conn, "PQprepare unnamed");
    }
    PQclear(res);
    drain_results(conn);

    res = PQexecPrepared(conn, "", 1, param_values, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        finish_with_error(conn, "PQexecPrepared unnamed");
    }
    printf("%s_ok rows=%d", tag, PQntuples(res));
    if (PQntuples(res) > 0)
        printf(" value=%s", PQgetvalue(res, 0, 0));
    printf("\n");
    fflush(stdout);
    PQclear(res);
    drain_results(conn);
}

int main(int argc, char **argv)
{
    PGconn *conn;

    if (argc < 4) {
        fprintf(stderr, "usage: %s <conninfo> <test_conf> <one|two>\n", argv[0]);
        return 1;
    }

    conn = PQconnectdb(argv[1]);
    if (PQstatus(conn) != CONNECTION_OK) {
        finish_with_error(conn, "PQconnectdb");
    }

    if (strcmp(argv[3], "one") == 0) {
        run_unnamed(
            conn,
            "select name from test where id = $1 /* gc_unnamed_one */",
            "1",
            "unnamed_one");
    } else if (strcmp(argv[3], "two") == 0) {
        run_unnamed(
            conn,
            "select name from test where id = $1 /* gc_unnamed_two */",
            "2",
            "unnamed_two");
    } else {
        fprintf(stderr, "unknown mode: %s\n", argv[3]);
        PQfinish(conn);
        return 1;
    }

    PQfinish(conn);
    return 0;
}
