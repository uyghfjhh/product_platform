import java.sql.*;

public class test_rep_hint_high_version {

    private static String Replica_Addr_1;
    private static String Replica_Addr_2;
    private static String Write_Node_Addr;
    private static String URL;
    private static String USER;
    private static String PASSWORD;

    private static final String INSERT_SQL = "INSERT INTO test (id, name) VALUES (?, ?)";
    private static final String DELETE_SQL = "DELETE FROM test WHERE name = ?";
    private static final String SELECT_BY_NAME_SQL = "SELECT * FROM test WHERE name = ?";
    private static final String SELECT_BY_Ids_Less_Than = "SELECT * FROM test WHERE id < ?";
    private static final String HEART_TEST_SQL = "SELECT ?";

    public static void main(String[] args) {

        Replica_Addr_1 = args[0];
        Replica_Addr_2 = args[1];
        Write_Node_Addr = args[2];
        URL = args[3];
        USER = args[4];
        PASSWORD = args[5];


        readOnlyTest();//1.1
        writeOnlyTest_1();//2.1
        writeOnlyTest_2();//2.2
        writeOnlyTest_3();//2.3
        readThenWriteTest_1();//3.1
        readThenWriteTest_2();//3.2
        writeThenReadTest_1();//4.1
        writeThenReadTest_2();//4.2
        heartTest();
    }

    //判断是否是读节点
    private static void is_read_node(Connection conn) throws SQLException {
        String serverAddr = getServerAddress(conn);//获取当前服务端ip与端口
        if (isInRecovery(conn) ) {
            System.out.println("读节点切换成功，当前读节点ip端口为: " + serverAddr);
        } 
        else
        {
            System.out.println("未处于正确读节点");
        }
    }

    //判断是否是写节点
    private static void is_write_node(Connection conn) throws SQLException {
        String serverAddr = getServerAddress(conn);//获取当前服务端ip与端口
        if (!isInRecovery(conn) ) {
            System.out.println("写节点切换成功，当前写节点ip端口为: " + serverAddr);
        } 
        else 
        {
            System.out.println("未处于正确写节点");
        }
    }


    // BEGIN READ ONLY + 请求
    private static void BeginReadOnly_Test(Connection conn, int input) throws SQLException {

        conn.setAutoCommit(false);    
        conn.setReadOnly(true);        // 切换到只读
        
        // 判断是否是读节点
        is_read_node(conn);

        selectByNames(conn, input);

        conn.commit(); // COMMIT
        conn.setReadOnly(false);
    }

    // BEGIN + 请求
    private static void Begin_Test(Connection conn, int input) throws SQLException {
        conn.setAutoCommit(false); // BEGIN
         // 构造插入数据
        insertByValues(conn, input);
        deleteByNames(conn, input);
         // 判断是否是写节点
        is_write_node(conn);
        deleteByNames(conn, input);
        conn.commit(); // COMMIT
    }

    // 隐式事务 
    private static void ImplicitTransaction_Test(Connection conn, int input) throws SQLException {    
        conn.setAutoCommit(true); 
        insertByValues(conn, input);
         // 判断是否是写节点
        is_write_node(conn);
        deleteByNames(conn, input);
    }

    private static void heartTest() {
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD)) {
            System.out.println("=== heartTest start ===");
            // 调用 3 次 select10086
            select10086(conn);
            select10086(conn);
            select10086(conn);
            conn.setAutoCommit(false);    
            conn.setReadOnly(true);        // 切换到只读
            System.out.println("switch to READ ONLY transaction");

            select10086(conn);
            select10086(conn);
            select10086(conn);

            conn.commit(); // COMMIT
            System.out.println("heartTest commit done");
            System.out.println("=== heartTest end ===");
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public static void select10086(Connection conn) {
        String sql = "SELECT ?";
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            pstmt.setInt(1, 10086);
            System.out.println("execute sql: SELECT 10086");
            try (ResultSet rs = pstmt.executeQuery()) {
                boolean hasRow = false;
                while (rs.next()) {
                    hasRow = true;
                    int value = rs.getInt(1);
                    System.out.println("query result: " + value);
                }
                if (!hasRow) {
                    System.out.println("query result: <no rows>");
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 1: 只发送到读节点
    private static void readOnlyTest() {
        System.out.println("=== Test 1.1 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            
            BeginReadOnly_Test(conn, 11);
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 2: 发送到写节点,两个隐式事务,不管客户端之前是否切换到只读节点
    private static void writeOnlyTest_1() {
        System.out.println("=== Test 2.1 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
            Statement stmt = conn.createStatement()) {
            conn.setAutoCommit(true); 
            insertByValues(conn, 21);

            // 判断是否是写节点
            is_write_node(conn);
            deleteByNames(conn, 21);
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 3:发送到写节点,显示事务 + 隐式事务,不管客户端之前是否切换到只读节点
    private static void writeOnlyTest_2() {
        System.out.println("=== Test 2.2 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            conn.setAutoCommit(false); 
             // 构造插入数据
            insertByValues(conn, 22);
            is_write_node(conn);
            conn.commit(); // COMMIT

            conn.setAutoCommit(true); 
            deleteByNames(conn, 22);
            
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 4:发送到写节点,显示事务 + 显式事务,不管客户端之前是否切换到只读节点
    private static void writeOnlyTest_3() {
        System.out.println("=== Test 2.3 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            conn.setAutoCommit(false); 
             // 构造插入数据
            insertByValues(conn, 23);
            is_write_node(conn);
            conn.commit(); // COMMIT

            conn.setAutoCommit(false); 
             // 构造插入数据
            deleteByNames(conn, 23);
            is_write_node(conn);
            conn.commit(); // COMMIT
           
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 5: BEGIN READ ONLY读 + 写隐式事务
    private static void readThenWriteTest_1() {
        System.out.println("=== Test 3.1 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            
            BeginReadOnly_Test(conn, 31);

            conn.setAutoCommit(true); 
            insertByValues(conn, 31);

            // 判断是否是写节点
            is_write_node(conn);
            deleteByNames(conn, 31);
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 6: BEGIN READ ONLY读 + 写显式事务
    private static void readThenWriteTest_2() {
        System.out.println("=== Test 3.2 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            
            BeginReadOnly_Test(conn, 32);
             conn.setAutoCommit(false); 
            insertByValues(conn, 32);

            // 判断是否是写节点
            is_write_node(conn);
            deleteByNames(conn, 32);
            conn.commit(); // COMMIT
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }


    // Test 7: 写隐式事务 + BEGIN READ ONLY读
    private static void writeThenReadTest_1() {
        System.out.println("=== Test 4.1 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            
            conn.setAutoCommit(true); 
            insertByValues(conn, 41);

            // 判断是否是写节点
            is_write_node(conn);
            deleteByNames(conn, 41);

            BeginReadOnly_Test(conn, 41);
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // Test 8: 写显式事务 + BEGIN READ ONLY读
    private static void writeThenReadTest_2() {
        System.out.println("=== Test 4.2 ===");
        try (Connection conn = DriverManager.getConnection(URL, USER, PASSWORD);
             Statement stmt = conn.createStatement()) {
            
            conn.setAutoCommit(false); 
            insertByValues(conn, 42);

            // 判断是否是写节点
            is_write_node(conn);
            deleteByNames(conn, 42);
            conn.commit(); // COMMIT

            BeginReadOnly_Test(conn, 42);
            System.out.println();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }


    // 如果是恢复模式则抛错
    public static void throwIfInRecovery(Connection conn) throws SQLException {
        boolean inRecovery = false;

        try (Statement stmt = conn.createStatement();
            ResultSet rs = stmt.executeQuery("SELECT pg_is_in_recovery()")) {
            if (rs.next()) {
                inRecovery = rs.getBoolean(1);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        if (inRecovery) {
            throw new IllegalStateException("当前处于恢复模式，无法继续执行。");
        }
    }

    // 如果不是恢复模式则抛错
    public static void throwIfNotInRecovery(Connection conn) throws SQLException {
        boolean inRecovery = false;

        try (Statement stmt = conn.createStatement();
            ResultSet rs = stmt.executeQuery("SELECT pg_is_in_recovery()")) {
            if (rs.next()) {
                inRecovery = rs.getBoolean(1);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        if (!inRecovery) {
            throw new IllegalStateException("当前不是恢复模式，无法继续执行。");
        }
    }


    public static void selectByNames(Connection conn, int input) {
        String[] names = buildNames(input, 0);
        try (PreparedStatement pstmt = conn.prepareStatement(SELECT_BY_NAME_SQL)) {
            for (String name : names) {
                pstmt.setString(1, name);

                try (ResultSet rs = pstmt.executeQuery()) {
                    System.out.println("=== 查询 name = " + name + " 的结果 ===");
                    printResult(rs);
                } catch (SQLException e) {
                    System.err.println("读取结果集时出错: " + e.getMessage());
                    throw new RuntimeException("执行 selectByNames 时 ResultSet 出错", e);
                }
            }
        } catch (SQLException e) {
            System.err.println("执行 selectByNames 出错: " + e.getMessage());
            throw new RuntimeException("执行 selectByNames 出错", e);
        }
    }

    public static void selectByIdsLessThan(Connection conn, int input) throws SQLException {
        int[] ids = {1, 2, 3};
        try (PreparedStatement pstmt = conn.prepareStatement(SELECT_BY_Ids_Less_Than)) {
            for (int id : ids) {
                pstmt.setInt(1, id);

                try (ResultSet rs = pstmt.executeQuery()) {
                    System.out.println("=== 查询 id < " + id + " 的结果 ===");
                    printResult(rs);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // 插入
    public static void insertByValues(Connection conn, int input) throws SQLException {
        Object[][] values = buildValues(input);
        try (PreparedStatement pstmt = conn.prepareStatement(INSERT_SQL)) {
            for (Object[] row : values) {
                pstmt.setInt(1, (Integer) row[0]);
                pstmt.setString(2, (String) row[1]);

                int affected = pstmt.executeUpdate();
                System.out.println("插入 id=" + row[0] + ", 影响行数=" + affected);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    // 删除
    public static void deleteByNames(Connection conn, int input) {
        String[] names = buildNames(input, 1);
        try (PreparedStatement pstmt = conn.prepareStatement(DELETE_SQL)) {
            for (String name : names) {
                pstmt.setString(1, name);

                int affected = pstmt.executeUpdate();
                System.out.println("删除 name=" + name + ", 影响行数=" + affected);
            }
        } catch (SQLException e) {
            System.err.println("执行 deleteByNames 出错: " + e.getMessage());
            throw new RuntimeException("执行 deleteByNames 出错", e);
        }
    }

    // 打印结果
    private static void printResult(ResultSet rs) throws SQLException {
        ResultSetMetaData meta = rs.getMetaData();
        int columnCount = meta.getColumnCount();

        while (rs.next()) {
            for (int i = 1; i <= columnCount; i++) {
                System.out.print(meta.getColumnName(i) + "=" + rs.getObject(i) + " ");
            }
            System.out.println();
        }
    }
    
    //获取服务端ip与端口
    public static String getServerAddress(Connection conn) throws SQLException {
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT inet_server_addr() AS server_ip, inet_server_port() AS server_port")) {
            if (rs.next()) {
                String ip = rs.getString("server_ip");
                int port = rs.getInt("server_port");
                return ip + ":" + port;
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return null;
    }

    //判断是否是恢复模式
    private static boolean isInRecovery(Connection conn) throws SQLException {
        String sql = "SELECT pg_is_in_recovery()"; // PostgreSQL 内置函数
        try (Statement stmt = conn.createStatement();
            ResultSet rs = stmt.executeQuery(sql)) {
            if (rs.next()) {
                return rs.getBoolean(1); // true = 在恢复模式(备库)，false = 主库
            } else {
                throw new SQLException("无法获取恢复模式状态");
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return false;
    }

    /** 根据 input 生成 id*/
    private static int[] buildIds(int input, int res) {
        if (res == 1) {
            input = input * 10;
        }

        return new int[] {
            input * 10 + 1,
            input * 10 + 2,
            input * 10 + 3
        };
    }

   /** 根据 input 生成 name，res为0输出read，否则输出write */
    private static String[] buildNames(int input, int res) {
        int first = input / 10;   
        int second = input % 10;  
        String prefix = (res == 0 ? "read_" : "write_") + first + "_" + second + "_";
        
        return new String[] {
            prefix + "1",
            prefix + "2",
            prefix + "3"
        };
    }

    /** 根据 input 生成 (id,name) 二维数组，便于 insert */
    private static Object[][] buildValues(int input) {
        int[] ids = buildIds(input, 1);
        String[] names = buildNames(input, 1);
        Object[][] values = new Object[ids.length][2];
        for (int i = 0; i < ids.length; i++) {
            values[i][0] = ids[i];
            values[i][1] = names[i];
        }
        return values;
    }

}

