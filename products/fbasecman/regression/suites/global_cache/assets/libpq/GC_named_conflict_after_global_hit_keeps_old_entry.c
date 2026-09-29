#include <libpq-fe.h>
#include <stdio.h>
#include <stdlib.h>

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

static void exec_named(PGconn *conn, const char *value, const char *tag)
{
    PGresult *res;
    const char *params[1] = {value};

    res = PQexecPrepared(conn, "stmt_conflict_keep", 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        finish_with_error(conn, tag);
    }
    if (PQntuples(res) > 0) {
        printf("%s=%s\n", tag, PQgetvalue(res, 0, 0));
    } else {
        printf("%s=OK\n", tag);
    }
    PQclear(res);
    drain_results(conn);
}

int main(int argc, char **argv)
{
    PGconn *conn;
    PGresult *res;
    Oid param_types[1] = {23};

    if (argc < 2) {
        fprintf(stderr, "usage: %s <conninfo> [test_conf]\n", argv[0]);
        return 1;
    }

    conn = PQconnectdb(argv[1]);
    if (PQstatus(conn) != CONNECTION_OK) {
        finish_with_error(conn, "PQconnectdb");
    }

    res = PQprepare(conn, "stmt_conflict_keep",
                    "select name from test where id = $1 /* gc_named_conflict_keep_old_one */",
                    1, param_types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        finish_with_error(conn, "PQprepare first");
    }
    PQclear(res);
    drain_results(conn);

    exec_named(conn, "1", "first_ok");

    res = PQprepare(conn, "stmt_conflict_keep",
                    "select name from test where id = $1 /* gc_named_conflict_keep_old_two */",
                    1, param_types);
    if (PQresultStatus(res) == PGRES_COMMAND_OK) {
        PQclear(res);
        fprintf(stderr, "expected named conflict but second prepare succeeded\n");
        PQfinish(conn);
        return 1;
    }
    printf("conflict_ok=%s\n", PQresultErrorMessage(res));
    PQclear(res);
    drain_results(conn);

    exec_named(conn, "2", "reuse_old_ok");

    PQfinish(conn);
    return 0;
}
