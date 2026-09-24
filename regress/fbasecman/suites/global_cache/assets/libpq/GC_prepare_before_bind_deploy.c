#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libpq-fe.h>

// t_test1/t_test2/t_test3 由 setup_env.sh 建表并插数；开头 DISCARD ALL 清会话状态，再 TRUNCATE+INSERT 复位
static void reset_test_data(PGconn *conn) {
    PGresult *res;

    res = PQexec(conn, "DISCARD ALL");
    PQclear(res);
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);

    res = PQexec(conn, "TRUNCATE t_test1, t_test2, t_test3");
    PQclear(res);
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);

    res = PQexec(conn, "INSERT INTO t_test1 VALUES (1, 'test1_row1'), (2, 'test1_row2')");
    PQclear(res);
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);
    res = PQexec(conn, "INSERT INTO t_test2 VALUES (10, 'test2_row1'), (20, 'test2_row2')");
    PQclear(res);
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);
    res = PQexec(conn, "INSERT INTO t_test3 VALUES (100, 'test3_row1'), (200, 'test3_row2')");
    PQclear(res);
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);

    printf("=== 测试数据已复位（与 setup_env 一致） ===\n\n");
}


// 事务一：PREPARE 语句，执行查询，设置只读模式
void transaction_1(PGconn *conn, const char *test_conf) {
    printf("=== 事务一开始 ===\n");
    printf("1. PREPARE stmt_q1 (查询 t_test1)\n");
    
    PGresult *res;
    
    // PREPARE stmt_q1 - 这个语句会被事务二复用
    Oid paramTypes[1] = {23};  // int4
    res = PQprepare(conn, "stmt_q1",
        "SELECT * FROM t_test1 WHERE id = $1",
        1, paramTypes);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        fprintf(stderr, "PREPARE stmt_q1 失败: %s\n", PQerrorMessage(conn));
        PQclear(res);
        return;
    }
    PQclear(res);
    printf("   stmt_q1 创建成功\n");
    
    // 执行 stmt_q1 查询
    printf("2. EXECUTE stmt_q1 (id=1)\n");
    const char *paramValues[1];
    char id_val[16] = "1";
    paramValues[0] = id_val;
    
    res = PQexecPrepared(conn, "stmt_q1", 1, paramValues, NULL, NULL, 0);
    if (PQresultStatus(res) == PGRES_TUPLES_OK && PQntuples(res) > 0) {
        printf("   查询结果: id=%s, name=%s\n", 
               PQgetvalue(res, 0, 0), 
               PQgetvalue(res, 0, 1));
    }
    PQclear(res);
    
    {
        int hint_mode = test_conf != NULL && test_conf[0] != '\0'
            && (strcmp(test_conf, "mmr_hint") == 0 || strcmp(test_conf, "rep_hint") == 0 || strcmp(test_conf, "unnamed_prepare_no") == 0);

        if (hint_mode) {
            printf("3. hint 模式 (%s): SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY\n",
                   test_conf);
            res = PQexec(conn, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY");
            if (PQresultStatus(res) != PGRES_COMMAND_OK) {
                fprintf(stderr, "设置只读模式失败: %s\n", PQerrorMessage(conn));
            } else {
                printf("   只读模式设置成功\n");
            }
            PQclear(res);
        } else {
            printf("3. 非 hint 模式%s: 等待 5s\n",
                   (test_conf && test_conf[0]) ? test_conf : "（未传 TEST_CONF）");
            sleep(5);
        }
    }
    
    printf("=== 事务一结束 ===\n\n");
}

// 事务二：用 Pipeline 模式查询，构造 PBDE BDE PBDES 报文
// BDE 复用事务一的 stmt_q1，不发 P
void transaction_2(PGconn *conn) {
    printf("=== 事务二开始 (Pipeline 模式) ===\n");
    printf("目标报文: PBDE(stmt_q2) BDE(复用stmt_q1) PBDES(stmt_q3)\n");
    printf("----------------------------------------\n");
    
    // 进入 Pipeline 模式
    if (PQenterPipelineMode(conn) != 1) {
        fprintf(stderr, "进入 Pipeline 模式失败\n");
        return;
    }
    
    // 构造报文序列: PBDE BDE PBDES
    // 注意：stmt_q1 是事务一创建的，这里复用，不发 P
    
    // 1. PBDE - 语句1: 查询 t_test2 (新语句，需要 P)
    Oid paramTypes_q2[1] = {23};  // int4
    int ret = PQsendPrepare(conn, "stmt_q2",
        "SELECT * FROM t_test2 WHERE key = $1",
        1, paramTypes_q2);
    if (ret == 0) {
        fprintf(stderr, "Parse(stmt_q2) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("1. P - Parse: stmt_q2 (SELECT * FROM t_test2 WHERE key = $1)\n");
    
    // B: Bind + E: Execute
    const char *param_q2[1];
    char key_val[16];
    snprintf(key_val, sizeof(key_val), "%d", 10);
    param_q2[0] = key_val;
    
    ret = PQsendQueryPrepared(conn, "stmt_q2", 1, param_q2, NULL, NULL, 0);
    if (ret == 0) {
        fprintf(stderr, "Bind/Execute(stmt_q2) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("   B - Bind: key=10\n");
    printf("   E - Execute: stmt_q2\n");
    
    // 2. BDE - 语句2: 复用 stmt_q1（事务一创建的），查询 t_test1
    // 关键点：这里不发 P（Parse），直接 B/E
    printf("2. (无P) - 复用事务一的 stmt_q1\n");
    
    const char *param_q1[1];
    char id_val[16];
    snprintf(id_val, sizeof(id_val), "%d", 2);  // 查 id=2，和事务一不同
    param_q1[0] = id_val;
    
    ret = PQsendQueryPrepared(conn, "stmt_q1", 1, param_q1, NULL, NULL, 0);
    if (ret == 0) {
        fprintf(stderr, "Bind/Execute(stmt_q1) 失败: %s\n", PQerrorMessage(conn));
        PQexitPipelineMode(conn);
        return;
    }
    printf("   B - Bind: id=2\n");
    printf("   E - Execute: stmt_q1 (复用，无Parse)\n");
    
    // 3. PBDES - 语句3: 查询 t_test3，然后 Sync
    Oid paramTypes_q3[1] = {23};  // int4
    ret = PQsendPrepare(conn, "stmt_q3",
        "SELECT * FROM t_test3 WHERE code = $1",
        1, paramTypes_q3);
    if (ret == 0) {
        fprintf(stderr, "Parse(stmt_q3) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("3. P - Parse: stmt_q3 (SELECT * FROM t_test3 WHERE code = $1)\n");
    
    // B: Bind
    const char *param_q3[1];
    char code_val[16];
    snprintf(code_val, sizeof(code_val), "%d", 100);
    param_q3[0] = code_val;
    
    ret = PQsendQueryPrepared(conn, "stmt_q3", 1, param_q3, NULL, NULL, 0);
    if (ret == 0) {
        fprintf(stderr, "Bind/Execute(stmt_q3) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("   B - Bind: code=100\n");
    printf("   E - Execute: stmt_q3\n");
    
    // S: Sync
    printf("   S - Sync\n");
    PQpipelineSync(conn);
    
    printf("----------------------------------------\n");
    printf("报文序列构造完成: P-B-D-E B-D-E P-B-D-E-S\n");
    printf("(第二个查询复用了事务一的 stmt_q1，没有发送 Parse)\n\n");
    
    // Flush 到网络（必须循环直到完全发送）
    {
        int flush_ret;
        while ((flush_ret = PQflush(conn)) == 1) {
            // 1 表示还有待发送数据，继续 flush
        }
        if (flush_ret == -1) {
            fprintf(stderr, "PQflush 失败: %s\n", PQerrorMessage(conn));
            PQexitPipelineMode(conn);
            return;
        }
    }
    
    // 读取结果
    printf("=== 读取查询结果 ===\n");
    int count = 0;
    PGresult *res;
    while (1) {
        while ((res = PQgetResult(conn)) != NULL) {
            count++;
            ExecStatusType status = PQresultStatus(res);
            printf("Result %d: %s(status=%d)", count, PQresStatus(status), (int)status);
            
            if (status == PGRES_TUPLES_OK && PQntuples(res) > 0) {
                printf(" -> ");
                for (int i = 0; i < PQntuples(res); i++) {
                    for (int j = 0; j < PQnfields(res); j++) {
                        printf("%s ", PQgetvalue(res, i, j));
                    }
                }
            } else if (status == PGRES_PIPELINE_SYNC) {
                printf(" <-- Sync 点");
            } else if (status == PGRES_COMMAND_OK) {
                // Parse 成功返回 COMMAND_OK
            } else {
                printf("\n  Error: %s", PQresultErrorMessage(res));
            }
            printf("\n");
            
            PQclear(res);
        }
        if (!PQisBusy(conn))
            break;
        PQconsumeInput(conn);
    }
    printf("\n总共 %d 条结果\n", count);
    
    printf("\n=== 事务二结束 ===\n\n");
}

int main(int argc, char *argv[]) {
    const char *conninfo;
    const char *test_conf = NULL;

    if (argc < 2) {
        fprintf(stderr, "用法: %s <libpq连接串> [TEST_CONF]\n", argv[0]);
        fprintf(stderr, "示例: %s \"host=127.0.0.1 port=17432 user=postgres dbname=postgres\" mmr_hint\n",
                argv[0]);
        return 1;
    }
    conninfo = argv[1];
    if (argc >= 3)
        test_conf = argv[2];

    PGconn *conn = PQconnectdb(conninfo);

    if (PQstatus(conn) != CONNECTION_OK) {
        fprintf(stderr, "Connection failed: %s\n", PQerrorMessage(conn));
        PQfinish(conn);
        return 1;
    }

    printf("============================================\n");
    printf("  Pipeline 报文测试: PBDE BDE PBDES\n");
    printf("============================================\n\n");

    reset_test_data(conn);

    transaction_1(conn, test_conf);
    
    // 执行事务二（Pipeline 模式，复用 stmt_q1）
    transaction_2(conn);
    
    // 清理
    PQfinish(conn);
    
    printf("============================================\n");
    printf("  测试完成\n");
    printf("============================================\n");
    
    return 0;
}
