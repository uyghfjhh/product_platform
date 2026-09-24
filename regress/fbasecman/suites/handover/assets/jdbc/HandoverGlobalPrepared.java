import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;

/** Executes the chapter 11.5 PreparedStatement read/write switch sequence. */
public final class HandoverGlobalPrepared {
    private static void pause(BufferedReader control, String phase) throws Exception {
        System.out.println("PHASE_PAUSE=" + phase);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("control channel closed at " + phase);
        }
    }

    private static void printBackend(Connection connection, String phase) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery");
             ResultSet result = statement.executeQuery()) {
            result.next();
            System.out.println(phase + " backend_port=" + result.getInt("backend_port")
                + " in_recovery=" + result.getBoolean("in_recovery"));
        }
    }

    private static void query(Connection connection, int id, String phase) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT note FROM handover_global_ps WHERE id = ? /* handover_global_ps */")) {
            statement.setInt(1, id);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    throw new IllegalStateException("missing prepared test row id=" + id);
                }
                System.out.println(phase + " id=" + id + " note=" + result.getString("note"));
            }
        }
    }

    private static void execute(Connection connection, String sql) throws Exception {
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.execute();
        }
    }

    private static void prepareRows(Connection connection) throws Exception {
        try (PreparedStatement delete = connection.prepareStatement(
                "DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)")) {
            System.out.println("SQL=DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)");
            delete.executeUpdate();
        }
        try (PreparedStatement insert = connection.prepareStatement(
                "INSERT INTO handover_global_ps(id, note) VALUES (?, ?)")) {
            for (int id = 1; id <= 3; id++) {
                String note = "name" + id;
                insert.setInt(1, id);
                insert.setString(2, note);
                System.out.println("SQL=INSERT INTO handover_global_ps(id, note) VALUES (" + id + ", '" + note + "')");
                insert.executeUpdate();
            }
        }
    }

    public static void main(String[] args) throws Exception {
        try (Connection connection = DriverManager.getConnection(args[0])) {
            BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
            System.out.println("JDBC_DRIVER=postgresql; prepareThreshold=1; all SQL use PreparedStatement");
            connection.setAutoCommit(true);
            prepareRows(connection);
            pause(control, "PREPARED_ROWS");
            query(connection, 1, "DEFAULT");
            printBackend(connection, "DEFAULT");
            pause(control, "DEFAULT");

            execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY");
            query(connection, 2, "READ_ONLY_ONE");
            printBackend(connection, "READ_ONLY_ONE");
            pause(control, "READ_ONLY_ONE");

            execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE");
            query(connection, 1, "READ_WRITE_ONE");
            printBackend(connection, "READ_WRITE_ONE");
            pause(control, "READ_WRITE_ONE");

            execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY");
            query(connection, 3, "READ_ONLY_TWO");
            printBackend(connection, "READ_ONLY_TWO");
            pause(control, "READ_ONLY_TWO");

            execute(connection, "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE");
            query(connection, 2, "READ_WRITE_TWO");
            printBackend(connection, "READ_WRITE_TWO");
            pause(control, "READ_WRITE_TWO");

            execute(connection, "BEGIN READ ONLY");
            query(connection, 3, "BEGIN_READ_ONLY");
            printBackend(connection, "BEGIN_READ_ONLY");
            pause(control, "BEGIN_READ_ONLY");
            System.out.println("GLOBAL_PS_SWITCH_OK");
        }
    }
}
