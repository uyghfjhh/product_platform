import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_heartbeat_reload_reclassifies_existing_normal_entry {
    private static void pause(BufferedReader control) throws Exception {
        System.out.println("PHASE=AFTER_EXECUTE");
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
        try (Connection conn = DriverManager.getConnection(url, props);
             PreparedStatement ps = conn.prepareStatement("SELECT 124")) {
            boolean hasResult = ps.execute();
            System.out.println("sql124_execute=" + hasResult);
            if (hasResult) {
                try (ResultSet rs = ps.getResultSet()) {
                    while (rs.next()) {
                        System.out.println("sql124_value=" + rs.getInt(1));
                    }
                }
            }
            pause(control);
        }
    }
}
