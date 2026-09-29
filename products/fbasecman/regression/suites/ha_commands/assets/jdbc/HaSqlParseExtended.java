import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

public final class HaSqlParseExtended {
    private static int queryInt(Connection connection, String sql, int value) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setInt(1, value);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalStateException("query returned no rows: " + sql);
                }
                return result.getInt(1);
            }
        }
    }

    private static void expectFailure(Connection connection) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement("SELECT ? / 0")) {
            statement.setInt(1, 1);
            statement.executeQuery();
            throw new IllegalStateException("division by zero unexpectedly succeeded");
        } catch (SQLException expected) {
            System.out.println("EXPECTED_ERROR=" + expected.getSQLState());
        }
    }

    public static void main(String[] args) throws Exception {
        try (Connection connection = DriverManager.getConnection(args[0], args[1], args[2])) {
            int parameter = queryInt(connection, "SELECT ?::int", 42);
            int readPort = queryInt(connection, "SELECT inet_server_port() + (? * 0)", 1);
            System.out.println("PARAM_VALUE=" + parameter);
            System.out.println("READ_PORT=" + readPort);

            connection.setAutoCommit(false);
            expectFailure(connection);
            connection.rollback();
            System.out.println("ROLLBACK_RECOVERY=OK");

            expectFailure(connection);
            connection.commit();
            System.out.println("COMMIT_RECOVERY=OK");

            try (PreparedStatement statement = connection.prepareStatement(
                    "CREATE TEMP TABLE ha_sql_parse_extended(id int)")) {
                statement.execute();
            }
            int writePort = queryInt(connection, "SELECT inet_server_port() + (? * 0)", 1);
            System.out.println("WRITE_PORT=" + writePort);
            connection.rollback();
        }
    }
}
