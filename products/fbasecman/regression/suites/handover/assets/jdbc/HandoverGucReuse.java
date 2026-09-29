import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

/** Chapter 11.2 two-client transaction-pool GUC reuse sequence. */
public final class HandoverGucReuse {
    private static void pause(BufferedReader control, String phase) throws Exception {
        System.out.println("PHASE_PAUSE=" + phase);
        System.out.flush();
        if (control.readLine() == null) {
            throw new IllegalStateException("control channel closed at " + phase);
        }
    }

    private static String backend(Connection connection, String label) throws Exception {
        try (Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery(
                 "SELECT pg_backend_pid() AS pid, current_setting('work_mem') AS work_mem")) {
            result.next();
            String value = result.getInt("pid") + "," + result.getString("work_mem");
            System.out.println(label + " backend=" + value);
            return value;
        }
    }

    private static int pid(String value) {
        return Integer.parseInt(value.substring(0, value.indexOf(',')));
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: HandoverGucReuse <jdbc-url>");
        }
        try (Connection first = DriverManager.getConnection(args[0]);
             Connection second = DriverManager.getConnection(args[0])) {
            BufferedReader control = new BufferedReader(new InputStreamReader(System.in));
            first.setAutoCommit(true);
            try (Statement statement = first.createStatement()) {
                System.out.println("SQL=SET work_mem = '8MB'");
                statement.execute("SET work_mem = '8MB'");
            }
            String firstInitial = backend(first, "FIRST_INITIAL");
            pause(control, "FIRST_INITIAL");

            second.setAutoCommit(false);
            String secondReuse = backend(second, "SECOND_REUSE");
            pause(control, "SECOND_REUSE");

            first.setAutoCommit(false);
            String firstAgain = backend(first, "FIRST_AGAIN");
            pause(control, "FIRST_AGAIN");
            first.commit();
            second.commit();
            pause(control, "COMMITS");

            if (pid(firstInitial) != pid(secondReuse)) {
                throw new IllegalStateException("second client did not reuse first backend");
            }
            if ("8MB".equals(secondReuse.substring(secondReuse.indexOf(',') + 1))) {
                throw new IllegalStateException("second client inherited first client GUC");
            }
            if (!"8MB".equals(firstAgain.substring(firstAgain.indexOf(',') + 1))) {
                throw new IllegalStateException("first client GUC was not synchronized to new backend");
            }
            System.out.println("GUC_REUSE_OK");
        }
    }
}
