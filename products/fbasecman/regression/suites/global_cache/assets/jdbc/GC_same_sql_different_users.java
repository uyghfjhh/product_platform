import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_same_sql_different_users {
    private static BufferedReader control;

    private static void runOnce(String url, String user, String password,
                                String sql, String label) throws Exception {
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");
        try (Connection conn = DriverManager.getConnection(url, props);
             PreparedStatement ps = conn.prepareStatement(sql)) {
            boolean hasResult = ps.execute();
            System.out.println(label + "_execute=" + hasResult);
            if (hasResult) {
                try (ResultSet rs = ps.getResultSet()) {
                    while (rs.next()) {
                        System.out.println(label + "_value=" + rs.getString(1));
                    }
                }
            }
        }
    }

    public static void main(String[] args) throws Exception {
        control = new BufferedReader(new InputStreamReader(System.in));
        runOnce(args[0], args[1], args[2], args[5], "user1");
        System.out.println("PHASE=AFTER_USER1");
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
        runOnce(args[0], args[3], args[4], args[5], "user2");
        System.out.println("PHASE=AFTER_USER2");
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
    }
}
