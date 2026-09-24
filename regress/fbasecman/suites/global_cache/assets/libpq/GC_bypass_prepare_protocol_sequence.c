#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libpq-fe.h>

// 构造报文: PBDE（语句1） D（语句2） PBDES（语句3）
// 语句2是纯 Describe（类似 JDBC getParameterMetaData），不发 Execute
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

// 事务一：执行一次，让 stmt_cached 被服务器缓存
// test_conf：来自 argv[2]（如 mmr_hint），用于 hint 时 SET READ ONLY，否则 sleep(5)
void transaction_1(PGconn *conn, const char *test_conf) {
    printf("=== 事务一开始 ===\n");
    printf("目标: 执行一次使 PreparedStatement 被服务器缓存\n");
    printf("（仿照 JDBC executeUpdate 建立缓存）\n\n");
    
    PGresult *res;
    
    // PREPARE stmt_cached
    Oid paramTypes[1] = {23};  // int4
    res = PQprepare(conn, "stmt_cached",
        "SELECT * FROM t_test1 WHERE id = $1",
        1, paramTypes);
    if (PQresultStatus(res) != PGRES_COMMAND_OK) {
        fprintf(stderr, "PREPARE 失败: %s\n", PQerrorMessage(conn));
        PQclear(res);
        return;
    }
    PQclear(res);
    printf("1. Parse (P): stmt_cached 创建成功\n");
    
    // 等待 Parse 结果
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);
    
    // 执行一次（让服务器缓存这个语句）
    printf("2. Execute (B/E/S): 执行一次建立缓存\n");
    const char *params[1];
    char id_val[16] = "1";
    params[0] = id_val;
    
    res = PQexecPrepared(conn, "stmt_cached", 1, params, NULL, NULL, 0);
    if (PQresultStatus(res) == PGRES_TUPLES_OK && PQntuples(res) > 0) {
        printf("   -> 执行结果: id=%s, name=%s\n", 
               PQgetvalue(res, 0, 0), PQgetvalue(res, 0, 1));
    }
    PQclear(res);
    printf("   -> stmt_cached 已被服务器缓存（有 name，无需再 Parse）\n");
    
    // 等待结果
    while ((res = PQgetResult(conn)) != NULL) PQclear(res);
    
    /* hint 模式(mmr_hint/rep_hint)：SET READ ONLY；其它：等待 5s */
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
    
    printf("\n=== 事务一结束 ===\n\n");
}

// 事务二：Pipeline 模式构造 PBDE D PBDES
// 语句2是纯 Describe（类似 getParameterMetaData），不发 Execute
void transaction_2(PGconn *conn) {
    printf("=== 事务二开始 ===\n");
    printf("目标报文: PBDE（语句1） D（语句2） PBDES（语句3）\n");
    printf("关键点: 语句2只发 Describe（类似 JDBC getParameterMetaData），不发 Execute\n\n");
    
    PGresult *res;
    int ret;
    
    // 进入 Pipeline 模式
    if (PQenterPipelineMode(conn) != 1) {
        fprintf(stderr, "进入 Pipeline 模式失败\n");
        return;
    }
    
    printf("----------------------------------------\n");
    
    // 语句1: PBDE（新语句 stmt_q2，查询 t_test2）
    Oid paramTypes_q2[1] = {23};
    ret = PQsendPrepare(conn, "stmt_q2",
        "SELECT * FROM t_test2 WHERE key = $1",
        1, paramTypes_q2);
    if (ret == 0) {
        fprintf(stderr, "Parse(stmt_q2) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("1. PBDE - 语句1 (查询 t_test2):\n");
    printf("   P - Parse: stmt_q2\n");
    
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
    printf("   D/E - Describe/Execute: stmt_q2\n");
    printf("   -> 报文: P/B/D/E\n\n");
    
    // 语句2: D（纯 Describe，复用 stmt_cached，类似 getParameterMetaData）
    // 关键点：只发 Describe，不发 Execute！
    printf("2. D - 语句2 (纯 Describe，类似 getParameterMetaData):\n");
    printf("   复用事务一的 stmt_cached（已有 name，无需 Parse）\n");
    printf("   -> 只发送 Describe (D) 获取参数元数据\n");
    printf("   -> 不发 Execute（不像语句1那样执行查询）\n");
    
    // 发送 Describe（获取参数描述）
    ret = PQsendDescribePrepared(conn, "stmt_cached");
    if (ret == 0) {
        fprintf(stderr, "Describe(stmt_cached) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("   D - Describe: stmt_cached\n");
    printf("   -> 报文: D\n\n");
    
    // 语句3: PBDES（新语句 stmt_q3，查询 t_test3，最后 Sync）
    // 这里的 S 也是语句2的边界（拿到 Describe 的结果）
    Oid paramTypes_q3[1] = {23};
    ret = PQsendPrepare(conn, "stmt_q3",
        "SELECT * FROM t_test3 WHERE code = $1",
        1, paramTypes_q3);
    if (ret == 0) {
        fprintf(stderr, "Parse(stmt_q3) 失败\n");
        PQexitPipelineMode(conn);
        return;
    }
    printf("3. PBDES - 语句3 (查询 t_test3):\n");
    printf("   P - Parse: stmt_q3\n");
    
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
    printf("   D/E - Describe/Execute: stmt_q3\n");
    
    // Sync（结束整个 pipeline，同时也是语句2 Describe 的边界）
    printf("   S - Sync\n");
    printf("   -> 报文: P/B/D/E/S\n");
    printf("   (这个 S 也是语句2 D 的边界，用来获取 Describe 结果)\n\n");
    
    PQpipelineSync(conn);
    
    printf("----------------------------------------\n");
    printf("完整报文序列:\n");
    printf("  语句1: P/B/D/E\n");
    printf("  语句2: D        <-- 纯 Describe，不发 Execute\n");
    printf("  语句3: P/B/D/E/S\n");
    printf("\n总序列: P/B/D/E  D  P/B/D/E/S\n");
    printf("(语句2复用 stmt_cached，只发 Describe 获取元数据)\n\n");
    
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
    printf("=== 读取结果 ===\n");
    int count = 0;
    int describe_result_received = 0;

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
                printf(" <-- Sync 点（语句2的 D 和语句3的边界）");
            } else if (status == PGRES_COMMAND_OK) {
                describe_result_received = 1;
                printf(" <-- Parse/Bind/Execute 成功");
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

    printf("================================================\n");
    printf("  Describe-only 测试: PBDE D PBDES\n");
    printf("================================================\n\n");

    reset_test_data(conn);

    transaction_1(conn, test_conf);
    
    // 事务二：Pipeline 模式
    // 语句2调用 getParameterMetaData，只发 Describe，不发 Execute
    transaction_2(conn);
    
    PQfinish(conn);
    
    printf("================================================\n");
    printf("  测试完成\n");
    printf("================================================\n");
    
    return 0;
}
