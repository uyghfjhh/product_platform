import java.sql.*;

public class test_rep_read_port {
    private static String url;
    private static String user;
    private static String password;

    public static void main(String[] args) {
        url = args[0];
        user = args[1];
        password = args[2];

        try (Connection conn = DriverManager.getConnection(url, user, password)) {

            // 1. SELECT * FROM pg_is_in_recovery();
            try (PreparedStatement pstmt = conn.prepareStatement("SELECT * FROM pg_is_in_recovery()")) {
                try (ResultSet rs = pstmt.executeQuery()) {
                    while (rs.next()) {
                        System.out.println("pg_is_in_recovery: " + rs.getBoolean(1));
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 2. show port
            try (PreparedStatement pstmt = conn.prepareStatement("SHOW port")) {
                try (ResultSet rs = pstmt.executeQuery()) {
                    while (rs.next()) {
                        System.out.println("port: " + rs.getString(1));
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 3. SELECT inet_server_addr();
            try (PreparedStatement pstmt = conn.prepareStatement("SELECT inet_server_addr()")) {
                try (ResultSet rs = pstmt.executeQuery()) {
                    while (rs.next()) {
                        System.out.println("inet_server_addr: " + rs.getString(1));
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            } catch (Exception e) {
                e.printStackTrace();
            }

            // 4. SELECT * FROM test WHERE id <= 3
            try (PreparedStatement pstmt = conn.prepareStatement("SELECT * FROM test WHERE id <= ?")) {
                pstmt.setInt(1, 3);
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

            // 5. INSERT INTO test VALUES (15,'test_1')
            try (PreparedStatement pstmt = conn.prepareStatement("INSERT INTO test VALUES (?, ?)")) {
                pstmt.setInt(1, 15);
                pstmt.setString(2, "test_1");
                int count = pstmt.executeUpdate();
                System.out.println("INSERT " + count);
            } catch (Exception e) {
                e.printStackTrace();
            }

        } catch (SQLException e) {
            e.printStackTrace();
        }
    }
}
