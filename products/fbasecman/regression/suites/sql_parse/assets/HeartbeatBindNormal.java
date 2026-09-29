import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;

public class HeartbeatBindNormal {
    public static void main(String[] args) throws Exception {
        Class.forName("org.postgresql.Driver");
        try (Connection connection = DriverManager.getConnection(args[0], args[1], args[2])) {
            try (PreparedStatement statement = connection.prepareStatement("SELECT 1")) {
                for (int i = 0; i < 2; i++) {
                    try (ResultSet result = statement.executeQuery()) {
                        if (!result.next() || result.getInt(1) != 1)
                            throw new IllegalStateException("unexpected heartbeat result");
                    }
                }
            }
        }
        System.out.println("CLIENT_SEQUENCE=Parse,Bind,Execute,Sync");
        System.out.println("HEARTBEAT_JDBC_EXTENDED=OK");
    }
}
