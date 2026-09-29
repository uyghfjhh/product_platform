import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_prepared_single_query {
    private static void pause(BufferedReader control, String marker) throws Exception {
        System.out.println(marker);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
    }

    public static void main(String[] args) throws Exception {
        Properties props = new Properties();
        props.setProperty("user", args[1]);
        props.setProperty("password", args[2]);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");
        String sql = args[3];
        String label = args[4];
        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        try (Connection conn = DriverManager.getConnection(args[0], props);
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
            pause(control, "PHASE=AFTER_EXECUTE");
        }
    }
}
