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
                int actual = result.getInt(1);
                if (result.wasNull() || result.next()) {
                    throw new IllegalStateException("expected exactly one non-null integer: " + sql);
                }
                return actual;
            }
        }
    }

    private static void expectFailure(Connection connection, String phase) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement("SELECT ? / 0")) {
            statement.setInt(1, 1);
            statement.executeQuery();
            throw new IllegalStateException("division by zero unexpectedly succeeded");
        } catch (SQLException expected) {
            if (!"22012".equals(expected.getSQLState())) {
                throw expected;
            }
            System.out.println(phase + "_ERROR=" + expected.getSQLState());
        }
    }

    public static void main(String[] args) throws Exception {
        try (Connection connection = DriverManager.getConnection(args[0], args[1], args[2])) {
            int parameter = queryInt(connection, "SELECT ?::int", 42);
            System.out.println("PARAM_VALUE=" + parameter);

            connection.setAutoCommit(false);
            expectFailure(connection, "ROLLBACK");
            connection.rollback();
            System.out.println("ROLLBACK_VALUE=" + queryInt(connection, "SELECT ?::int", 42));
            System.out.println("ROLLBACK_RECOVERY=OK");

            expectFailure(connection, "COMMIT");
            connection.commit();
            System.out.println("COMMIT_VALUE=" + queryInt(connection, "SELECT ?::int", 42));
            System.out.println("COMMIT_RECOVERY=OK");

            connection.rollback();
        }
    }
}
