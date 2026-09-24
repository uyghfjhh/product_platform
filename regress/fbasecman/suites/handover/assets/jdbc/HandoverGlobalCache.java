import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;

/** Exact JDBC operations from the chapter 11.5 global-cache examples. */
public final class HandoverGlobalCache {
    private static void pause(BufferedReader control, String phase) throws Exception {
        System.out.println("PHASE_PAUSE=" + phase);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("control channel closed at " + phase);
        }
    }

    private static void execute(Connection connection, String sql) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            boolean hasResult = statement.execute();
            if (hasResult) {
                try (ResultSet result = statement.getResultSet()) {
                    while (result.next()) {
                        // The test-specific query helpers print their result values.
                    }
                }
            }
            System.out.println("SQL=" + sql);
        }
    }

    private static void query(Connection connection, String sql, int value, int expected) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setInt(1, value);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next() || result.getInt(1) != expected) {
                    throw new IllegalStateException("unexpected result for " + sql);
                }
                System.out.println("SQL=" + sql + " PARAM=" + value + " RESULT=" + result.getInt(1));
            }
        }
    }

    private static void normalRow(Connection connection) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT id, note FROM handover_global_ps WHERE id = ?")) {
            statement.setInt(1, 1);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalStateException("missing handover_global_ps id=1");
                }
                System.out.println("SQL=SELECT id, note FROM handover_global_ps WHERE id = ? PARAM=1");
            }
        }
    }

    private static void heartbeat(Connection connection) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement("SELECT 1");
             ResultSet result = statement.executeQuery()) {
            if (!result.next() || result.getInt(1) != 1) {
                throw new IllegalStateException("heartbeat did not return 1");
            }
            System.out.println("SQL=SELECT 1");
        }
    }

    private static void special(Connection connection, BufferedReader control) throws Exception {
        normalRow(connection);
        execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY");
        execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE");
        pause(control, "SPECIAL_ROUTING");

        connection.setAutoCommit(false);
        execute(connection, "BEGIN READ ONLY");
        execute(connection, "ROLLBACK");
        execute(connection, "BEGIN");
        execute(connection, "COMMIT");
        execute(connection, "BEGIN");
        execute(connection, "ROLLBACK");
        execute(connection, "BEGIN");
        execute(connection, "END");
        connection.setAutoCommit(true);
        pause(control, "SPECIAL_TRANSACTIONS");

        heartbeat(connection);
        heartbeat(connection);
        pause(control, "SPECIAL_HEARTBEAT");
        execute(connection, "SET application_name = 'global_ps_sql_class_test'");
        execute(connection, "SET application_name = 'global_ps_sql_class_test'");
        execute(connection, "SET search_path = public");
        execute(connection, "SET search_path = public");
        execute(connection, "RESET application_name");
        execute(connection, "RESET search_path");
        execute(connection, "RESET ALL");
        execute(connection, "DISCARD ALL");
        pause(control, "SPECIAL_GUC");
        System.out.println("SPECIAL_OK");
    }

    private static void evict(Connection connection, BufferedReader control) throws Exception {
        query(connection, "SELECT (? + 1)::int", 1, 2);
        pause(control, "EVICT_1");
        query(connection, "SELECT (? - 1)::int", 2, 1);
        pause(control, "EVICT_2");
        query(connection, "SELECT (? * 2)::int", 3, 6);
        pause(control, "EVICT_3");
        query(connection, "SELECT (? + 3 - 1)::int", 4, 6);
        pause(control, "EVICT_4");
        System.out.println("EVICT_OK");
    }

    private static void bypass(Connection connection, BufferedReader control) throws Exception {
        for (int value = 1; value <= 5; value++) {
            execute(connection, "SET application_name = 'global_ps_bypass_" + value + "'");
        }
        pause(control, "BYPASS_GUC_FIRST");
        query(connection, "SELECT (? + 10)::int", 1, 11);
        pause(control, "BYPASS_NORMAL_1");
        query(connection, "SELECT (? + 20)::int", 2, 22);
        pause(control, "BYPASS_NORMAL_2");
        query(connection, "SELECT (? + 30)::int", 3, 33);
        pause(control, "BYPASS_NORMAL_3");
        for (int value = 1; value <= 5; value++) {
            execute(connection, "SET application_name = 'global_ps_bypass_" + value + "'");
        }
        pause(control, "BYPASS_GUC_SECOND");
        System.out.println("BYPASS_OK");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: HandoverGlobalCache <url> <special|evict|bypass>");
        }
        try (Connection connection = DriverManager.getConnection(args[0])) {
            BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
            System.out.println("JDBC_DRIVER=postgresql; prepareThreshold=1; all SQL use PreparedStatement");
            if ("special".equals(args[1])) {
                special(connection, control);
            } else if ("evict".equals(args[1])) {
                evict(connection, control);
            } else if ("bypass".equals(args[1])) {
                bypass(connection, control);
            } else {
                throw new IllegalArgumentException("unknown mode " + args[1]);
            }
        }
    }
}
