import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_basic_reuse {
    private static void pause(BufferedReader control, String marker) throws Exception {
        System.out.println(marker);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
    }

    public static void main(String[] args) throws Exception {
        String url = args[0];
        String user = args[1];
        String password = args[2];
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");

        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        try (Connection conn = DriverManager.getConnection(url, props)) {
            String sql = "select name from test where id = ? /* gc_basic_reuse */";
            for (int i = 0; i < 3; i++) {
                try (PreparedStatement ps = conn.prepareStatement(sql)) {
                    ps.setInt(1, i + 1);
                    boolean hasResult = ps.execute();
                    System.out.println("iter=" + i + " hasResult=" + hasResult);
                    if (hasResult) {
                        try (ResultSet rs = ps.getResultSet()) {
                            while (rs.next()) {
                                System.out.println("value=" + rs.getString(1));
                            }
                        }
                    }
                }
                if (i == 0) {
                    pause(control, "PHASE=AFTER_FIRST");
                }
            }
        }
    }
}
