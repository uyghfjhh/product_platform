import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.Properties;

/** Executes the complete supported HA command family through a JDBC console connection. */
public final class HaConsoleCommands {
    private HaConsoleCommands() {
    }

    private static void execute(Statement statement, String sql, String marker) throws Exception {
        statement.execute(sql);
        System.out.println(marker + "=OK");
    }

    private static String query(Statement statement, String sql) throws Exception {
        StringBuilder output = new StringBuilder();
        try (ResultSet result = statement.executeQuery(sql)) {
            ResultSetMetaData metadata = result.getMetaData();
            while (result.next()) {
                for (int column = 1; column <= metadata.getColumnCount(); column++) {
                    if (column > 1) {
                        output.append('|');
                    }
                    String value = result.getString(column);
                    output.append(value == null ? "" : value);
                }
                output.append('\n');
            }
        }
        return output.toString();
    }

    private static void require(String text, String... values) {
        for (String value : values) {
            if (!text.contains(value)) {
                throw new IllegalStateException("missing value '" + value + "' in:\n" + text);
            }
        }
    }

    private static void snapshot(Path config, Path directory, String name) throws Exception {
        Files.copy(config, directory.resolve(name), StandardCopyOption.REPLACE_EXISTING);
    }

    private static int backendPort(String url, Properties properties) throws Exception {
        try (Connection connection = DriverManager.getConnection(url, properties);
             Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery("SELECT inet_server_port();")) {
            if (!result.next()) {
                throw new IllegalStateException("business route returned no backend port");
            }
            return result.getInt(1);
        }
    }

    private static void waitBackendPort(String url, Properties properties,
                                        String marker, int... expected) throws Exception {
        Exception lastError = null;
        for (int attempt = 0; attempt < 50; attempt++) {
            try {
                int actual = backendPort(url, properties);
                boolean matched = false;
                for (int port : expected) {
                    matched |= actual == port;
                }
                if (matched) {
                    System.out.println(marker + "=" + actual);
                    return;
                }
                lastError = new IllegalStateException(
                    "expected one of " + java.util.Arrays.toString(expected)
                    + ", got " + actual);
            } catch (Exception error) {
                lastError = error;
            }
            Thread.sleep(100L);
        }
        throw new IllegalStateException(marker + " did not converge", lastError);
    }

    private static void waitBackendPort(String url, Properties properties,
                                        int expected, String marker) throws Exception {
        waitBackendPort(url, properties, marker, expected);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 10) {
            throw new IllegalArgumentException(
                "usage: HaConsoleCommands <console-url> <user> <password> " +
                "<config> <snapshot-dir> <business-url> <single-url> " +
                "<cluster1-port> <cluster2-port> <standby1-port>");
        }

        Properties properties = new Properties();
        properties.setProperty("user", args[1]);
        properties.setProperty("password", args[2]);
        properties.setProperty("connectTimeout", "5");
        properties.setProperty("socketTimeout", "10");
        Properties businessProperties = new Properties();
        businessProperties.setProperty("user", "postgres");
        businessProperties.setProperty("password", "");
        businessProperties.setProperty("connectTimeout", "5");
        businessProperties.setProperty("socketTimeout", "10");

        Path config = Paths.get(args[3]);
        Path snapshotDirectory = Paths.get(args[4]);
        String businessUrl = args[5];
        String singleUrl = args[6];
        int cluster1Port = Integer.parseInt(args[7]);
        int cluster2Port = Integer.parseInt(args[8]);
        int standby1Port = Integer.parseInt(args[9]);
        Files.createDirectories(snapshotDirectory);

        try (Connection connection = DriverManager.getConnection(args[0], properties);
             Statement statement = connection.createStatement()) {
            require(query(statement, "SHOW DATASOURCES;"), "pg_3", "active");
            System.out.println("JDBC_CONNECT=OK");

            execute(statement, "SET NODE PARTED pg_3;", "SET_NODE_PARTED");
            require(query(statement, "SHOW DATASOURCES;"), "pg_3", "parted");
            snapshot(config, snapshotDirectory, "01_node_parted.conf");
            waitBackendPort(singleUrl, businessProperties,
                            "PARTED_FALLS_BACK_TO_PRIMARY", cluster1Port);
            execute(statement, "SET NODE ACTIVE pg_3;", "SET_NODE_ACTIVE");
            require(query(statement, "SHOW DATASOURCES;"), "pg_3", "active");
            snapshot(config, snapshotDirectory, "02_node_active.conf");
            waitBackendPort(singleUrl, businessProperties,
                            "ACTIVE_RESTORES_SINGLE_ROUTE", cluster1Port, standby1Port);

            execute(statement, "SET NODE WEIGHT pg_3=0;", "SET_NODE_WEIGHT");
            require(query(statement, "SHOW NODES;"), "pg_3", "0");
            snapshot(config, snapshotDirectory, "03_weight_0.conf");
            waitBackendPort(singleUrl, businessProperties,
                            "ZERO_WEIGHT_FALLS_BACK_TO_PRIMARY", cluster1Port);
            execute(statement, "SET NODE WEIGHT pg_3=10;", "SET_NODE_WEIGHT_RESTORE");
            require(query(statement, "SHOW NODES;"), "pg_3", "10");
            snapshot(config, snapshotDirectory, "04_weight_10.conf");
            waitBackendPort(singleUrl, businessProperties,
                            "WEIGHT_RESTORES_SINGLE_ROUTE", cluster1Port, standby1Port);

            execute(statement, "SET NODE PROMOTED pg_1 IN GROUP mmr_group;", "SET_NODE_PROMOTED");
            require(query(statement, "SHOW GROUP_ROUTING mmr_group;"), "pg_cluster_1");
            snapshot(config, snapshotDirectory, "05_promoted_cluster_1.conf");
            waitBackendPort(businessUrl, businessProperties, cluster2Port,
                            "PROMOTED_KEEPS_WRITE_ROUTE");

            execute(statement, "SET NODE WRITE pg_1 IN GROUP mmr_group;", "SET_NODE_WRITE");
            require(query(statement, "SHOW GROUP_ROUTING mmr_group;"), "pg_cluster_1");
            snapshot(config, snapshotDirectory, "06_write_cluster_1.conf");
            waitBackendPort(businessUrl, businessProperties, cluster1Port,
                            "WRITE_ROUTE_CLUSTER_1");
            execute(statement, "SET NODE WRITE pg_2 IN GROUP mmr_group;", "SET_NODE_WRITE_RESTORE");
            require(query(statement, "SHOW GROUP_ROUTING mmr_group;"), "pg_cluster_2");
            snapshot(config, snapshotDirectory, "07_write_cluster_2.conf");
            waitBackendPort(businessUrl, businessProperties, cluster2Port,
                            "WRITE_ROUTE_CLUSTER_2");

            execute(statement, "SET CLUSTER PARTED pg_cluster_2;", "SET_CLUSTER_PARTED");
            require(query(statement, "SHOW DATASOURCES;"), "pg_cluster_2", "parted");
            snapshot(config, snapshotDirectory, "08_cluster_2_parted.conf");
            waitBackendPort(businessUrl, businessProperties, cluster1Port,
                            "PARTED_ROUTE_PROMOTED_CLUSTER");
            execute(statement, "SET CLUSTER ACTIVE pg_cluster_2;", "SET_CLUSTER_ACTIVE");
            require(query(statement, "SHOW DATASOURCES;"), "pg_cluster_2", "active");
            snapshot(config, snapshotDirectory, "09_cluster_2_active.conf");
            waitBackendPort(businessUrl, businessProperties, cluster2Port,
                            "ACTIVE_ROUTE_WRITE_CLUSTER");

            execute(statement, "REFRESH CLUSTER pg_cluster_1;", "REFRESH_CLUSTER");
            // A refresh may merge with an in-flight probe and endpoint
            // diagnostics publish asynchronously, so probe_seq is not a
            // per-command completion counter.
            require(query(statement, "SHOW GROUP_ROUTING mmr_group;"), "pg_cluster_2");
            snapshot(config, snapshotDirectory, "10_after_refresh.conf");
            System.out.println("ALL_HA_COMMANDS=OK");
        }
    }
}
