import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.Properties;

public class GC_discard_all_redeploy {
    private static void pause(BufferedReader control, String marker) throws Exception {
        System.out.println(marker);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("controller closed before resume: " + marker);
        }
    }

    private static void query(Connection conn, String sql, String label) throws Exception {
        boolean returned = false;
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setInt(1, 1);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    returned = true;
                    System.out.println(label + "=" + rs.getString(1));
                }
            }
        }
        if (!returned) {
            System.out.println(label + "=OK");
        }
    }

    public static void main(String[] args) throws Exception {
        Properties props = new Properties();
        props.setProperty("user", args[1]);
        props.setProperty("password", args[2]);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");
        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        try (Connection conn = DriverManager.getConnection(args[0], props)) {
            System.out.println("autocommit=" + conn.getAutoCommit());
            query(conn, args[3], "before_discard");
            pause(control, "PHASE=BEFORE_DISCARD");
            try (Statement st = conn.createStatement()) {
                st.execute("DISCARD ALL");
            }
            pause(control, "PHASE=AFTER_DISCARD");
            query(conn, args[3], "after_redeploy");
        }
        System.out.println("PHASE=DONE");
    }
}
