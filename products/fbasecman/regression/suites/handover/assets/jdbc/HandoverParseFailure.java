import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.io.BufferedReader;
import java.io.InputStreamReader;

/** Chapter 11.4: prove failed Parse cache entries are discarded before reuse. */
public final class HandoverParseFailure {
    private static void pause(BufferedReader control, String marker) throws Exception {
        System.out.println(marker);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("controller closed before resume: " + marker);
        }
    }
    private static final class BackendInfo {
        final String address;
        final int port;
        final int pid;

        BackendInfo(String address, int port, int pid) {
            this.address = address;
            this.port = port;
            this.pid = pid;
        }
    }

    /* Matches the document sample: backend inspection is also a PreparedStatement. */
    private static BackendInfo backendInfo(Connection connection) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT inet_server_addr(), inet_server_port(), pg_backend_pid()");
             ResultSet result = statement.executeQuery()) {
            result.next();
            return new BackendInfo(result.getString(1), result.getInt(2), result.getInt(3));
        }
    }

    private static void executeSingle(Connection connection, BufferedReader control) throws Exception {
        System.out.println("JDBC_DRIVER=postgresql; prepareThreshold=1; PreparedStatement");
        System.out.println("STEP1=drop test table");
        try (Statement statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS handover_parse_single");
        }
        connection.setAutoCommit(false);
        BackendInfo before = backendInfo(connection);
        System.out.println("STEP2=PreparedStatement PBDES against missing table");
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT id FROM handover_parse_single WHERE id = ?")) {
            statement.setInt(1, 1);
            statement.executeQuery();
            throw new IllegalStateException("missing-table Parse unexpectedly succeeded");
        } catch (SQLException expected) {
            System.out.println("EXPECTED_ERROR=" + expected.getMessage());
        }
        connection.rollback();
        System.out.println("TRANSACTION_ROLLED_BACK");
        connection.setAutoCommit(true);
        System.out.println("STEP3=hold failed Parse state for console and log observation");
        pause(control, "PHASE_READY=AFTER_SINGLE_PARSE_FAILURE");
        System.out.println("STEP4=create table and insert document data");
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE handover_parse_single(id int primary key, note text)");
            statement.execute("INSERT INTO handover_parse_single VALUES (1, 'test1'), (2, 'test2')");
        }
        connection.setAutoCommit(false);
        BackendInfo after = backendInfo(connection);
        System.out.println("STEP5=reuse backend and create a new PreparedStatement with identical SQL");
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT id, note FROM handover_parse_single WHERE id = ?")) {
            statement.setInt(1, 2);
            try (ResultSet result = statement.executeQuery()) {
                result.next();
                System.out.println("RECOVERED_ROW=id=" + result.getInt(1) + ",name=" + result.getString(2));
            }
        }
        connection.commit();
        System.out.println("backend_before=" + before.address + ":" + before.port + " pid=" + before.pid);
        System.out.println("backend_after=" + after.address + ":" + after.port + " pid=" + after.pid);
        System.out.println("backend_reused=" + (before.pid == after.pid));
        System.out.println("SINGLE_OK");
    }

    private static void executeMultiple(Connection connection, BufferedReader control) throws Exception {
        System.out.println("JDBC_DRIVER=postgresql; prepareThreshold=1; createStatement");
        try (Statement statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS handover_parse_multi_1");
            statement.execute("DROP TABLE IF EXISTS handover_parse_multi_2");
            statement.execute("CREATE TABLE handover_parse_multi_1(id int primary key, note text)");
            statement.execute("INSERT INTO handover_parse_multi_1 VALUES (1, 'one'), (3, 'three')");
        }
        connection.setAutoCommit(false);
        BackendInfo before = backendInfo(connection);
        String sql = "SELECT note FROM handover_parse_multi_1 WHERE id = 1; "
            + "SELECT note FROM handover_parse_multi_2 WHERE id = 2; "
            + "SELECT note FROM handover_parse_multi_1 WHERE id = 3";
        try (Statement statement = connection.createStatement()) {
            statement.execute(sql);
            throw new IllegalStateException("middle missing-table Parse unexpectedly succeeded");
        } catch (SQLException expected) {
            System.out.println("EXPECTED_ERROR=" + expected.getMessage());
        }
        connection.rollback();
        System.out.println("TRANSACTION_ROLLED_BACK");
        connection.setAutoCommit(true);
        System.out.println("STEP3=hold failed Parse state for console and log observation");
        pause(control, "PHASE_READY=AFTER_MULTIPLE_PARSE_FAILURE");
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE handover_parse_multi_2(id int primary key, note text)");
            statement.execute("INSERT INTO handover_parse_multi_2 VALUES (2, 'two')");
        }
        connection.setAutoCommit(false);
        BackendInfo after = backendInfo(connection);
        int resultSets = 0;
        try (Statement statement = connection.createStatement()) {
            boolean hasResult = statement.execute(sql);
            while (true) {
                if (hasResult) {
                    try (ResultSet result = statement.getResultSet()) {
                        result.next();
                        System.out.println("MULTI_ROW=" + result.getString(1));
                        resultSets++;
                    }
                }
                if (!statement.getMoreResults() && statement.getUpdateCount() == -1) {
                    break;
                }
                hasResult = true;
            }
        }
        connection.commit();
        if (resultSets != 3) {
            throw new IllegalStateException("expected three result sets, got " + resultSets);
        }
        System.out.println("backend_before=" + before.address + ":" + before.port + " pid=" + before.pid);
        System.out.println("backend_after=" + after.address + ":" + after.port + " pid=" + after.pid);
        System.out.println("backend_reused=" + (before.pid == after.pid));
        System.out.println("MULTIPLE_OK");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: HandoverParseFailure <url> <single|multiple>");
        }
        BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
        try (Connection connection = DriverManager.getConnection(args[0])) {
            if ("single".equals(args[1])) {
                executeSingle(connection, control);
            } else if ("multiple".equals(args[1])) {
                executeMultiple(connection, control);
            } else {
                throw new IllegalArgumentException("unknown mode " + args[1]);
            }
        }
    }
}
