import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

/**
 * Executes every routing sequence listed in chapter 9.  Phase names retain
 * the document's group/case numbering so report.txt can be checked against
 * the source without inferring coverage from a generic happy-path run.
 */
public final class HandoverJdbcRouting {
    private static final BufferedReader CONTROL = new BufferedReader(new InputStreamReader(System.in));

    private static void pause(String phase) throws Exception {
        System.out.println("PHASE_PAUSE=" + phase);
        System.out.flush();
        String command = CONTROL.readLine();
        if (!"continue".equals(command)) {
            throw new IllegalStateException("expected continue after " + phase + ", got " + command);
        }
    }

    private static void printBackend(Connection connection, String phase) throws SQLException {
        try (Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery(
                 "SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery")) {
            result.next();
            System.out.println(phase + " backend_port=" + result.getInt("backend_port")
                + " in_recovery=" + result.getBoolean("in_recovery"));
        }
    }

    private static void executeThreeWrites(Connection connection, String phase) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            statement.execute("CREATE TABLE IF NOT EXISTS handover_jdbc_route(id int primary key)");
            int base = phase.hashCode() & 0x7fffffff;
            statement.execute("INSERT INTO handover_jdbc_route VALUES (" + base + ") ON CONFLICT DO NOTHING");
            statement.execute("UPDATE handover_jdbc_route SET id = id WHERE id = " + base);
            statement.execute("DELETE FROM handover_jdbc_route WHERE id = " + base);
            System.out.println(phase + " dml1=INSERT dml2=UPDATE dml3=DELETE");
        }
    }

    private static void executeThreeReads(Connection connection, String phase) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            for (int value = 1; value <= 3; value++) {
                try (ResultSet result = statement.executeQuery(
                        "SELECT " + value + " AS read_value, pg_is_in_recovery() AS in_recovery")) {
                    result.next();
                    System.out.println(phase + " read" + value + "=" + result.getInt("read_value")
                        + " in_recovery=" + result.getBoolean("in_recovery"));
                }
            }
        }
    }

    private static void readPhase(Connection connection, String phase) throws SQLException {
        printBackend(connection, phase + "_READ_BEFORE");
        executeThreeReads(connection, phase);
        printBackend(connection, phase + "_READ_AFTER");
    }

    private static void writePhase(Connection connection, String phase) throws SQLException {
        printBackend(connection, phase + "_WRITE_BEFORE");
        executeThreeWrites(connection, phase);
        printBackend(connection, phase + "_WRITE_AFTER");
    }

    private static void done(String phase) {
        System.out.println("PHASE_OK " + phase);
    }

    private static void oldReadOnlyAuto(Connection connection, String phase) throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(true);
        readPhase(connection, phase);
        done(phase);
    }

    private static void oldReadOnlyTransaction(Connection connection, String phase, boolean twice)
            throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(true);
        connection.setAutoCommit(false);
        readPhase(connection, phase + "_TXN1");
        connection.commit();
        if (twice) {
            readPhase(connection, phase + "_TXN2");
            connection.commit();
        }
        connection.setReadOnly(false);
        connection.setAutoCommit(true);
        done(phase);
    }

    private static void oldWriteAuto(Connection connection, String phase) throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(false);
        writePhase(connection, phase);
        done(phase);
    }

    private static void oldWriteTransaction(Connection connection, String phase, boolean twice)
            throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(false);
        connection.setAutoCommit(false);
        writePhase(connection, phase + "_TXN1");
        connection.commit();
        if (twice) {
            writePhase(connection, phase + "_TXN2");
            connection.commit();
        }
        connection.setAutoCommit(true);
        done(phase);
    }

    private static void oldReadThenWrite(Connection connection, String phase, boolean readTransactions,
                                         boolean writeTransactions, boolean repeatedTransactions)
            throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(true);
        if (readTransactions) {
            connection.setAutoCommit(false);
            readPhase(connection, phase + "_READ_TXN1");
            connection.commit();
            if (repeatedTransactions) {
                readPhase(connection, phase + "_READ_TXN2");
                connection.commit();
            }
            connection.setAutoCommit(true);
        } else {
            readPhase(connection, phase + "_READ");
        }
        connection.setReadOnly(false);
        if (writeTransactions) {
            connection.setAutoCommit(false);
            writePhase(connection, phase + "_WRITE_TXN1");
            connection.commit();
            if (repeatedTransactions) {
                writePhase(connection, phase + "_WRITE_TXN2");
                connection.commit();
            }
            connection.setAutoCommit(true);
        } else {
            writePhase(connection, phase + "_WRITE");
        }
        done(phase);
    }

    private static void oldWriteThenRead(Connection connection, String phase, boolean writeTransactions,
                                         boolean readTransactions, boolean repeatedTransactions,
                                         boolean forceReadWriteFirst) throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(false);
        if (forceReadWriteFirst) {
            connection.setReadOnly(false);
        }
        if (writeTransactions) {
            connection.setAutoCommit(false);
            writePhase(connection, phase + "_WRITE_TXN1");
            connection.commit();
            if (repeatedTransactions) {
                writePhase(connection, phase + "_WRITE_TXN2");
                connection.commit();
            }
            connection.setAutoCommit(true);
        } else {
            writePhase(connection, phase + "_WRITE");
        }
        connection.setReadOnly(true);
        if (readTransactions) {
            connection.setAutoCommit(false);
            readPhase(connection, phase + "_READ_TXN1");
            connection.commit();
            if (repeatedTransactions) {
                readPhase(connection, phase + "_READ_TXN2");
                connection.commit();
            }
            connection.setAutoCommit(true);
        } else {
            readPhase(connection, phase + "_READ");
        }
        connection.setReadOnly(false);
        done(phase);
    }

    private static void oldDriverMatrix(Connection connection) throws Exception {
        // 9.1.2 1.1: all listed READ ONLY variants.
        oldReadOnlyAuto(connection, "OLD_11_1");
        oldReadOnlyTransaction(connection, "OLD_11_2", false);
        oldReadOnlyTransaction(connection, "OLD_11_3", true);
        pause("OLD_11");

        // 9.1.2 1.2: default and explicit READ WRITE writer variants.
        oldWriteAuto(connection, "OLD_12_1");
        oldWriteTransaction(connection, "OLD_12_2", false);
        oldWriteTransaction(connection, "OLD_12_3", true);
        oldWriteAuto(connection, "OLD_12_RW_1");
        oldWriteTransaction(connection, "OLD_12_RW_2", false);
        oldWriteTransaction(connection, "OLD_12_RW_3", true);
        pause("OLD_12");

        // 9.1.2 1.3: four read-then-write variants.
        oldReadThenWrite(connection, "OLD_13_1", false, false, false);
        oldReadThenWrite(connection, "OLD_13_2", true, false, false);
        oldReadThenWrite(connection, "OLD_13_3", false, true, false);
        oldReadThenWrite(connection, "OLD_13_4", true, true, true);
        pause("OLD_13");

        // 9.1.2 1.4: four normal plus two explicit READ WRITE variants.
        oldWriteThenRead(connection, "OLD_14_1", false, false, false, false);
        oldWriteThenRead(connection, "OLD_14_2", true, false, false, false);
        oldWriteThenRead(connection, "OLD_14_3", false, true, false, false);
        oldWriteThenRead(connection, "OLD_14_4", true, true, true, false);
        oldWriteThenRead(connection, "OLD_14_RW_1", false, false, false, true);
        oldWriteThenRead(connection, "OLD_14_RW_2", true, true, true, true);
        pause("OLD_14");
        System.out.println("OLD_MATRIX_OK");
    }

    private static void newReadOnly(Connection connection, String phase) throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(true);
        connection.setAutoCommit(false);
        readPhase(connection, phase + "_READ_TXN");
        connection.commit();
        connection.setReadOnly(false);
        connection.setAutoCommit(true);
        done(phase);
    }

    private static void newWrite(Connection connection, String phase, boolean transactions, boolean twice)
            throws SQLException {
        connection.setAutoCommit(true);
        connection.setReadOnly(false);
        if (transactions) {
            connection.setAutoCommit(false);
            writePhase(connection, phase + "_WRITE_TXN1");
            connection.commit();
            if (twice) {
                writePhase(connection, phase + "_WRITE_TXN2");
                connection.commit();
            }
            connection.setAutoCommit(true);
        } else {
            writePhase(connection, phase + "_WRITE");
        }
        done(phase);
    }

    private static void newReadThenWrite(Connection connection, String phase, boolean writerTransaction)
            throws SQLException {
        newReadOnly(connection, phase + "_READ");
        newWrite(connection, phase + "_WRITE", writerTransaction, false);
        done(phase);
    }

    private static void newWriteThenRead(Connection connection, String phase, boolean writerTransaction)
            throws SQLException {
        newWrite(connection, phase + "_WRITE", writerTransaction, false);
        newReadOnly(connection, phase + "_READ");
        done(phase);
    }

    private static void newDriverMatrix(Connection connection) throws Exception {
        // 9.2.2: every documented BEGIN READ ONLY / BEGIN combination.
        newReadOnly(connection, "NEW_11_1");
        pause("NEW_11");
        newWrite(connection, "NEW_12_1", false, false);
        newWrite(connection, "NEW_12_2", true, false);
        newWrite(connection, "NEW_12_3", true, true);
        pause("NEW_12");
        newReadThenWrite(connection, "NEW_13_1", false);
        newReadThenWrite(connection, "NEW_13_2", true);
        pause("NEW_13");
        newWriteThenRead(connection, "NEW_14_1", false);
        newWriteThenRead(connection, "NEW_14_2", true);
        pause("NEW_14");
        System.out.println("NEW_MATRIX_OK");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: HandoverJdbcRouting <jdbc-url> <old|new>");
        }
        try (Connection connection = DriverManager.getConnection(args[0])) {
            if ("old".equals(args[1])) {
                oldDriverMatrix(connection);
            } else if ("new".equals(args[1])) {
                newDriverMatrix(connection);
            } else {
                throw new IllegalArgumentException("unsupported JDBC routing mode: " + args[1]);
            }
        }
    }
}
