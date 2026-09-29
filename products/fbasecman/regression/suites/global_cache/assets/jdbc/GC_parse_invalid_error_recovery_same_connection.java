import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_parse_invalid_error_recovery_same_connection {
    private static final String TABLE_NAME = "test_parse_error";
    private static final String SELECT_SQL = "SELECT * FROM " + TABLE_NAME + " WHERE id = ? ";

    private static String[] backendInfo(Connection conn) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT inet_server_addr() as server_ip, inet_server_port() as server_port");
             ResultSet rs = ps.executeQuery()) {
            if (rs.next()) {
                return new String[] {rs.getString("server_ip"), String.valueOf(rs.getInt("server_port"))};
            }
        }
        throw new SQLException("cannot fetch backend info");
    }

    private static Connection open(String url, String user, String password) throws SQLException {
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("binaryTransfer", "true");
        return DriverManager.getConnection(url, props);
    }

    private static void pause(BufferedReader control, String marker) throws Exception {
        System.out.println(marker);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            throw new IllegalArgumentException("usage: <url> <user> <password>");
        }
        String url = args[0];
        String user = args[1];
        String password = args[2];
        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));

        try (Connection admin = open(url, "postgres", "");
             Statement adminStmt = admin.createStatement()) {
            admin.setAutoCommit(false);
            adminStmt.execute("DROP TABLE IF EXISTS " + TABLE_NAME);
            admin.commit();
        }

        try (Connection conn = open(url, user, password)) {
            conn.setAutoCommit(false);
            try (PreparedStatement ps = conn.prepareStatement(SELECT_SQL)) {
                String[] firstBackend = backendInfo(conn);
                System.out.println("步骤0: 查看后端连接信息");
                System.out.println("  IP: " + firstBackend[0]);
                System.out.println("  Port: " + firstBackend[1]);

                try {
                    ps.setInt(1, 1);
                    ps.executeQuery();
                    throw new SQLException("expected first execution failure");
                } catch (SQLException e) {
                    System.out.println("符合预期,首次执行失败！");
                    conn.commit();
                }
                pause(control, "PHASE=AFTER_FIRST_FAILURE");

                try (Connection admin = open(url, "postgres", "");
                     Statement adminStmt = admin.createStatement()) {
                    admin.setAutoCommit(false);
                    adminStmt.execute("CREATE TABLE " + TABLE_NAME + " (id INT PRIMARY KEY, data char(10))");
                    adminStmt.execute("INSERT INTO " + TABLE_NAME + "(id,data) VALUES (1,'test1'),(2,'test2')");
                    adminStmt.execute("GRANT SELECT ON TABLE " + TABLE_NAME + " TO " + user);
                    admin.commit();
                }
                pause(control, "PHASE=AFTER_CREATE");

                String[] secondBackend = backendInfo(conn);
                System.out.println("步骤3: 再次查看后端连接信息");
                System.out.println("  IP: " + secondBackend[0]);
                System.out.println("  Port: " + secondBackend[1]);
                if (firstBackend[0].equals(secondBackend[0]) && firstBackend[1].equals(secondBackend[1])) {
                    System.out.println("复用了之前的后端连接！");
                } else {
                    System.out.println("没有复用之前的后端连接！");
                }

                System.out.println("步骤4: 再次执行PreparedStatement,应执行成功");
                ps.clearParameters();
                ps.setInt(1, 2);
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        System.out.println("查询结果: id=" + rs.getInt("id") + ",data=" + rs.getString("data"));
                    }
                }
                conn.commit();
                pause(control, "PHASE=AFTER_RECOVERY");
            }
        }

        try (Connection admin = open(url, "postgres", "");
             Statement adminStmt = admin.createStatement()) {
            admin.setAutoCommit(false);
            adminStmt.execute("DROP TABLE IF EXISTS " + TABLE_NAME);
            admin.commit();
        }

        System.out.println("SUCCESS: 未触发Parse缓存清理问题");
    }
}
