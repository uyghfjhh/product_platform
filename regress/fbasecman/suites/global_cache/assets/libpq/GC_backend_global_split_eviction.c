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

static void exec_sql(PGconn *conn, const char *sql, const char *tag)
{
    PGresult *res = PQexec(conn, sql);
    if (PQresultStatus(res) != PGRES_COMMAND_OK && PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        finish_with_error(conn, tag);
    }
    PQclear(res);
    drain_results(conn);
}

static void prepare_and_exec(PGconn *conn, const char *name, const char *sql, int value, const char *tag)
{
    PGresult *res;
    char param_buf[32];
    const char *params[1];
    Oid param_types[1] = {23};

    snprintf(param_buf, sizeof(param_buf), "%d", value);
    params[0] = param_buf;

    res = PQprepare(conn, name, sql, 1, param_types);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        PQclear(res);
        finish_with_error(conn, tag);
    }
    PQclear(res);
    drain_results(conn);

    res = PQexecPrepared(conn, name, 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) != PGRES_TUPLES_OK) {
        PQclear(res);
        finish_with_error(conn, tag);
    }
    if (PQntuples(res) > 0) {
        printf("%s=%s\n", tag, PQgetvalue(res, 0, 0));
    }
    PQclear(res);
    drain_results(conn);
}

static void run_seed(const char *conninfo)
{
    PGconn *conn = PQconnectdb(conninfo);
    if (PQstatus(conn) != CONNECTION_OK) {
        finish_with_error(conn, "PQconnectdb seed");
    }

    exec_sql(conn, "DISCARD ALL", "DISCARD ALL");
    exec_sql(conn, "BEGIN", "BEGIN");
    printf("seed_begin_ok=true\n");

    prepare_and_exec(conn, "stmt_q1", "SELECT $1::int /* gc_split_initial_1 */", 1, "seed_q1");
    prepare_and_exec(conn, "stmt_q2", "SELECT $1::int /* gc_split_initial_2 */", 2, "seed_q2");
    prepare_and_exec(conn, "stmt_q3", "SELECT $1::int /* gc_split_initial_3 */", 3, "seed_q3");
    prepare_and_exec(conn, "stmt_q4", "SELECT $1::int /* gc_split_initial_4 */", 4, "seed_q4");
    prepare_and_exec(conn, "stmt_q5", "SELECT $1::int /* gc_split_initial_5 */", 5, "seed_q5");

    exec_sql(conn, "COMMIT", "COMMIT");
    printf("seed_commit_ok=true\n");
    PQfinish(conn);
}

static void run_trigger(const char *conninfo)
{
    PGconn *conn = PQconnectdb(conninfo);
    if (PQstatus(conn) != CONNECTION_OK) {
        finish_with_error(conn, "PQconnectdb trigger");
    }

    prepare_and_exec(conn, "stmt_q6", "SELECT $1::int /* gc_split_pressure_6 */", 6, "trigger_q6");
    printf("trigger_done=true\n");
    PQfinish(conn);
}

int main(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr, "usage: %s <conninfo> <test_conf> <seed|trigger>\n", argv[0]);
        return 1;
    }

    if (strcmp(argv[3], "seed") == 0) {
        run_seed(argv[1]);
        return 0;
    }
    if (strcmp(argv[3], "trigger") == 0) {
        run_trigger(argv[1]);
        return 0;
    }

    fprintf(stderr, "unknown mode: %s\n", argv[3]);
    return 1;
}
