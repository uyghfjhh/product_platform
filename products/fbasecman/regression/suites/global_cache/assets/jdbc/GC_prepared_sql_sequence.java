import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.Properties;

public class GC_prepared_sql_sequence {
    private static void pause(BufferedReader control, String outputKey) throws Exception {
        System.out.println("PHASE=AFTER_" + outputKey);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("phase control closed");
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length <= 3 || (args.length - 3) % 3 != 0) {
            throw new IllegalArgumentException(
                    "usage: <url> <user> <password> (<output-key> <statement|execute|query|query_int:N|query_columns:k1,k2> <sql>)+");
        }
        Properties props = new Properties();
        props.setProperty("user", args[1]);
        props.setProperty("password", args[2]);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");

        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        try (Connection conn = DriverManager.getConnection(args[0], props)) {
            for (int i = 3; i < args.length; i += 3) {
                String outputKey = args[i];
                String mode = args[i + 1];
                String sql = args[i + 2];
                if ("statement".equals(mode)) {
                    try (Statement st = conn.createStatement()) {
                        System.out.println(outputKey + "=" + st.execute(sql));
                    }
                    pause(control, outputKey);
                    continue;
                }
                try (PreparedStatement ps = conn.prepareStatement(sql)) {
                    String[] columnKeys = null;
                    if (mode.startsWith("query_int:")) {
                        ps.setInt(1, Integer.parseInt(mode.substring("query_int:".length())));
                        mode = "query";
                    } else if (mode.startsWith("execute_int:")) {
                        ps.setInt(1, Integer.parseInt(mode.substring("execute_int:".length())));
                        mode = "execute";
                    } else if (mode.startsWith("query_columns:")) {
                        columnKeys = mode.substring("query_columns:".length()).split(",");
                        mode = "query";
                    }
                    if ("execute".equals(mode)) {
                        System.out.println(outputKey + "=" + ps.execute());
                    } else if ("query".equals(mode)) {
                        try (ResultSet rs = ps.executeQuery()) {
                            while (rs.next()) {
                                if (columnKeys != null) {
                                    if (columnKeys.length != rs.getMetaData().getColumnCount()) {
                                        throw new IllegalArgumentException(
                                                "query_columns key count does not match result column count");
                                    }
                                    for (int column = 1; column <= columnKeys.length; column++) {
                                        System.out.println(columnKeys[column - 1] + "=" + rs.getString(column));
                                    }
                                    continue;
                                }
                                StringBuilder row = new StringBuilder();
                                for (int column = 1; column <= rs.getMetaData().getColumnCount(); column++) {
                                    if (column > 1) row.append('|');
                                    row.append(rs.getString(column));
                                }
                                System.out.println(outputKey + "=" + row);
                            }
                        }
                    } else {
                        throw new IllegalArgumentException("unknown operation mode: " + mode);
                    }
                    pause(control, outputKey);
                }
            }
        }
    }
}
