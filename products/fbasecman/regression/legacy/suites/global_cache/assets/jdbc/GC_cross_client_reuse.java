import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_cross_client_reuse {
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
        String sql = "select name from test where id = ? /* gc_cross_client_reuse */";

        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        for (int i = 0; i < 2; i++) {
            try (Connection conn = DriverManager.getConnection(url, props)) {
                try (PreparedStatement ps = conn.prepareStatement(sql)) {
                    ps.setInt(1, 1);
                    try (ResultSet rs = ps.executeQuery()) {
                        while (rs.next()) {
                            System.out.println("cross_client=" + rs.getString(1));
                        }
                    }
                }
            }
            if (i == 0) {
                pause(control, "PHASE=AFTER_CLIENT1");
            }
        }
    }
}
