import java.sql.*;

public class test_mmr_read_port {
    private static String url;
    private static String user;
    private static String password;

    public static void main(String[] args) {
        url = args[0];
        user = args[1];
        password = args[2];

        try (Connection conn = DriverManager.getConnection(url, user, password)) {
            conn.setAutoCommit(false); // BEGIN
            System.out.println("BEGIN;");

            // 第一次检查恢复模式（不打印）
            boolean inRecovery = false;
            try (Statement stmt = conn.createStatement();
                 ResultSet rs = stmt.executeQuery("SELECT pg_is_in_recovery()")) {
                if (rs.next()) {
                    inRecovery = rs.getBoolean(1);
                }
            }

            if (!inRecovery) {
                runTest1(conn);
            } else {
                runTest2(conn);
            }

            conn.commit();
            System.out.println("COMMIT;");

        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static void runTest1(Connection conn) throws SQLException {
        // 1. SELECT * FROM pg_is_in_recovery(); (需要打印)
        printRecoveryStatus(conn);

        // 2. show port
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT inet_server_port()")) {
            while (rs.next()) {
                System.out.println("PORT: " + rs.getInt(1));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 3. SELECT inet_server_addr();
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT inet_server_addr()")) {
            while (rs.next()) {
                System.out.println("ADDR: " + rs.getString(1));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 4. INSERT
        try (PreparedStatement pstmt = conn.prepareStatement("INSERT INTO test VALUES (?, ?)")) {
            pstmt.setInt(1, 15);
            pstmt.setString(2, "test_1");
            System.out.println("INSERT " + pstmt.executeUpdate());
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 5. SELECT * WHERE id = 15
        printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

        // 6. UPDATE
        try (PreparedStatement pstmt = conn.prepareStatement("UPDATE test SET name = ? WHERE id = ?")) {
            pstmt.setString(1, "test_2");
            pstmt.setInt(2, 15);
            System.out.println("UPDATE " + pstmt.executeUpdate());
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 7. SELECT * WHERE id = 15
        printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

        // 8. DELETE
        try (PreparedStatement pstmt = conn.prepareStatement("DELETE FROM test WHERE id = ?")) {
            pstmt.setInt(1, 15);
            System.out.println("DELETE " + pstmt.executeUpdate());
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 9. SELECT * WHERE id = 15
        printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

        // 10. SELECT * WHERE id <= 3
        printSelect(conn, "SELECT * FROM test WHERE id <= ?", 3);
    }

    private static void runTest2(Connection conn) throws SQLException {
        // 1. SELECT * FROM pg_is_in_recovery(); (需要打印)
        printRecoveryStatus(conn);

        // 2. show port
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT inet_server_port()")) {
            while (rs.next()) {
                System.out.println("PORT: " + rs.getInt(1));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 3. SELECT inet_server_addr();
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT inet_server_addr()")) {
            while (rs.next()) {
                System.out.println("ADDR: " + rs.getString(1));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }

        // 4. SELECT * WHERE id <= 3
        printSelect(conn, "SELECT * FROM test WHERE id <= ?", 3);

        // 5. INSERT
        try (PreparedStatement pstmt = conn.prepareStatement("INSERT INTO test VALUES (?, ?)")) {
            pstmt.setInt(1, 15);
            pstmt.setString(2, "test_1");
            System.out.println("INSERT " + pstmt.executeUpdate());
        } catch (Exception e) {
            e.printStackTrace();
        }

    }

    private static void printRecoveryStatus(Connection conn) throws SQLException {
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT pg_is_in_recovery()")) {
            while (rs.next()) {
                System.out.println("pg_is_in_recovery: " + rs.getBoolean(1));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static void printSelect(Connection conn, String sql, int param) throws SQLException {
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            pstmt.setInt(1, param);
            try (ResultSet rs = pstmt.executeQuery()) {
                ResultSetMetaData meta = rs.getMetaData();
                int columnCount = meta.getColumnCount();
                boolean hasRow = false;
                while (rs.next()) {
                    hasRow = true;
                    for (int i = 1; i <= columnCount; i++) {
                        System.out.print(rs.getString(i) + (i < columnCount ? "\t" : ""));
                    }
                    System.out.println();
                }
                if (!hasRow) {
                    System.out.println("(0 rows)");
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
