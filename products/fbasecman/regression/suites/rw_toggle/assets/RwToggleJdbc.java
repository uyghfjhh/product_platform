import java.sql.*;

public class RwToggleJdbc {
    public static void main(String[] args) throws Exception {
        String url = args[0];
        String mode = args[3];
        String writeUrl = "port".equals(mode) ? args[4] : url;
        try (Connection c = DriverManager.getConnection(
                "port".equals(mode) ? url : writeUrl, args[1], args[2])) {
            c.setReadOnly(true);
            c.setAutoCommit(false);
            try (Statement s = c.createStatement(); ResultSet r = s.executeQuery(
                    "select inet_server_port(), 10086")) {
                r.next();
                System.out.println("READ_PORT=" + r.getInt(1));
                System.out.println("HEARTBEAT=" + r.getInt(2));
            }
            c.commit();
            c.setAutoCommit(true);
            try (Statement s = c.createStatement(); ResultSet r = s.executeQuery(
                    "select inet_server_port()")) {
                r.next();
                System.out.println("READ_AGAIN=" + r.getInt(1));
            }
        }
        try (Connection c = DriverManager.getConnection(writeUrl, args[1], args[2])) {
            c.setReadOnly(false);
            c.setAutoCommit(false);
            try (Statement s = c.createStatement()) {
                s.execute("create temporary table rw_toggle_jdbc_probe(id integer)");
            }
            try (PreparedStatement p = c.prepareStatement(
                    "select inet_server_port()" ); ResultSet r = p.executeQuery()) {
                r.next();
                System.out.println("WRITE_PORT=" + r.getInt(1));
            }
            c.commit();
            c.setAutoCommit(true);
            try (Statement s = c.createStatement(); ResultSet r = s.executeQuery(
                    "select inet_server_port()")) {
                r.next();
                System.out.println("WRITE_AGAIN=" + r.getInt(1));
            }
        }
    }
}
