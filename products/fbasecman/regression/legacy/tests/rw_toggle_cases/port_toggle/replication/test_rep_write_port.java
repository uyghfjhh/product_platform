import java.sql.*;

public class test_rep_write_port {
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

            // 1. show port (替代为函数调用)
            try (Statement stmt = conn.createStatement();
                 ResultSet rs = stmt.executeQuery("SELECT inet_server_port() AS port")) {
                while (rs.next()) {
                    System.out.println("PORT: " + rs.getInt("port"));
                }
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 2. SELECT inet_server_addr();
            try (Statement stmt = conn.createStatement();
                 ResultSet rs = stmt.executeQuery("SELECT inet_server_addr() AS addr")) {
                while (rs.next()) {
                    System.out.println("ADDR: " + rs.getString("addr"));
                }
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 3. INSERT
            try (PreparedStatement pstmt = conn.prepareStatement("INSERT INTO test VALUES (?, ?)")) {
                pstmt.setInt(1, 15);
                pstmt.setString(2, "test_1");
                int count = pstmt.executeUpdate();
                System.out.println("INSERT " + count);
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 4. SELECT * WHERE id = 15
            printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

            // 5. UPDATE
            try (PreparedStatement pstmt = conn.prepareStatement("UPDATE test SET name = ? WHERE id = ?")) {
                pstmt.setString(1, "test_2");
                pstmt.setInt(2, 15);
                int count = pstmt.executeUpdate();
                System.out.println("UPDATE " + count);
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 6. SELECT * WHERE id = 15
            printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

            // 7. DELETE
            try (PreparedStatement pstmt = conn.prepareStatement("DELETE FROM test WHERE id = ?")) {
                pstmt.setInt(1, 15);
                int count = pstmt.executeUpdate();
                System.out.println("DELETE " + count);
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 8. SELECT * WHERE id = 15
            printSelect(conn, "SELECT * FROM test WHERE id = ?", 15);

            // 9. SELECT * WHERE id <= 3
            printSelect(conn, "SELECT * FROM test WHERE id <= ?", 3);

            conn.commit(); // COMMIT
            System.out.println("COMMIT;");

        } catch (SQLException e) {
            e.printStackTrace();
        }
    }

    // 打印 SELECT 查询结果
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
