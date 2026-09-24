import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

public class GC_phased_prepared {
    public static void main(String[] args) throws Exception {
        if (args.length < 6 || (args.length - 3) % 3 != 0) {
            throw new IllegalArgumentException(
                "usage: <url> <user> <password> (<output-key> <sql> <int-value>)+"
            );
        }

        Properties props = new Properties();
        props.setProperty("user", args[1]);
        props.setProperty("password", args[2]);
        props.setProperty("prepareThreshold", "1");
        props.setProperty("preferQueryMode", "extended");

        List<Connection> connections = new ArrayList<>();
        List<PreparedStatement> statements = new ArrayList<>();
        try {
            for (int i = 3; i < args.length; i += 3) {
                String outputKey = args[i];
                String sql = args[i + 1];
                int value = Integer.parseInt(args[i + 2]);
                Connection connection = DriverManager.getConnection(args[0], props);
                PreparedStatement statement = connection.prepareStatement(sql);
                connections.add(connection);
                statements.add(statement);
                statement.setInt(1, value);
                try (ResultSet result = statement.executeQuery()) {
                    while (result.next()) {
                        System.out.println(outputKey + "=" + result.getString(1));
                    }
                }
            }

            System.out.println("PHASE=READY");
            System.out.flush();
            String command = new BufferedReader(new InputStreamReader(System.in)).readLine();
            if (!"continue".equals(command)) {
                throw new IllegalArgumentException("expected stdin command: continue");
            }
            System.out.println("PHASE=DONE");
        } finally {
            for (PreparedStatement statement : statements) {
                try { statement.close(); } catch (Exception ignored) { }
            }
            for (Connection connection : connections) {
                try { connection.close(); } catch (Exception ignored) { }
            }
        }
    }
}
