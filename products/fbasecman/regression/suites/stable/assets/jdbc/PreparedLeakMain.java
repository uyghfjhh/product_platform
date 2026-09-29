import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Properties;
import java.util.Random;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

public class PreparedLeakMain {
    private static final String JDBC_URL = System.getenv().getOrDefault("JDBC_URL", "jdbc:postgresql://127.0.0.1:26432/mmrhint");
    private static final String JDBC_USER = System.getenv().getOrDefault("JDBC_USER", "mmrhint");
    private static final String JDBC_PASSWORD = System.getenv().getOrDefault("JDBC_PASSWORD", "fexbase1_@#$.");
    private static final String JDBC_PREPARE_THRESHOLD = System.getenv().getOrDefault("JDBC_PREPARE_THRESHOLD", "1");
    private static final String JDBC_PS_CACHE_QUERIES = System.getenv().getOrDefault("JDBC_PS_CACHE_QUERIES", "0");
    private static final long REPORT_INTERVAL_MS = 60_000L;

    public static void main(String[] args) throws Exception {
        Config config = Config.parse(args);
        long seed = System.currentTimeMillis();
        Random seedRandom = new Random(seed);
        List<String> sharedTemplates = buildSharedSqlTemplates(config.sharedSqlCount);

        log("CONFIG workload=%s duration=%ss long_clients=%d short_clients=%d shared_sql_count=%d private_sql_count=%d short_batch=%d short_idle_ms=%d url=%s prepare_threshold=%s ps_cache_queries=%s seed=%d",
            config.workloadName, config.durationSeconds, config.longClients, config.shortClients, config.sharedSqlCount,
            config.privateSqlCount, config.shortBatch, config.shortIdleMs, JDBC_URL, JDBC_PREPARE_THRESHOLD, JDBC_PS_CACHE_QUERIES, seed);

        AtomicBoolean stop = new AtomicBoolean(false);
        AtomicLong success = new AtomicLong(0);
        AtomicLong failures = new AtomicLong(0);
        AtomicLong prepares = new AtomicLong(0);
        AtomicLong rwSwitches = new AtomicLong(0);
        AtomicLong rwSwitchFailures = new AtomicLong(0);
        AtomicLong heartbeats = new AtomicLong(0);
        AtomicLong heartbeatFailures = new AtomicLong(0);
        AtomicLong gucOps = new AtomicLong(0);
        AtomicLong gucFailures = new AtomicLong(0);
        CountDownLatch latch = new CountDownLatch(config.longClients + config.shortClients);

        List<Thread> workers = new ArrayList<>();
        for (int i = 0; i < config.longClients; i++) {
            final int clientId = i;
            List<String> privateTemplates = buildPrivateSqlTemplates(clientId, "long", config.privateSqlCount);
            Thread thread = new Thread(() -> runLongClient(clientId, sharedTemplates, privateTemplates, new Random(seedRandom.nextLong()), config, stop, success, failures, prepares, rwSwitches, rwSwitchFailures, heartbeats, heartbeatFailures, gucOps, gucFailures, latch), "jdbc-long-" + i);
            thread.start();
            workers.add(thread);
        }
        for (int i = 0; i < config.shortClients; i++) {
            final int clientId = i;
            List<String> privateTemplates = buildPrivateSqlTemplates(clientId, "short", config.privateSqlCount);
            Thread thread = new Thread(() -> runShortClient(clientId, sharedTemplates, privateTemplates, new Random(seedRandom.nextLong()), config, stop, success, failures, prepares, rwSwitches, rwSwitchFailures, heartbeats, heartbeatFailures, gucOps, gucFailures, latch), "jdbc-short-" + i);
            thread.start();
            workers.add(thread);
        }

        long startMs = System.currentTimeMillis();
        long deadlineMs = startMs + config.durationSeconds * 1000L;
        long nextReport = startMs + REPORT_INTERVAL_MS;
        while (System.currentTimeMillis() < deadlineMs) {
            Thread.sleep(1000L);
            if (System.currentTimeMillis() >= nextReport) {
                log("PROGRESS elapsed=%ds success=%d failures=%d prepares=%d rw_switches=%d rw_switch_failures=%d heartbeats=%d heartbeat_failures=%d guc_ops=%d guc_failures=%d",
                    (System.currentTimeMillis() - startMs) / 1000L,
                    success.get(), failures.get(), prepares.get(), rwSwitches.get(), rwSwitchFailures.get(),
                    heartbeats.get(), heartbeatFailures.get(), gucOps.get(), gucFailures.get());
                nextReport += REPORT_INTERVAL_MS;
            }
        }

        stop.set(true);
        latch.await(30, TimeUnit.SECONDS);
        for (Thread thread : workers) {
            thread.join(1000L);
        }

        boolean passed = failures.get() == 0 && rwSwitchFailures.get() == 0 &&
            heartbeatFailures.get() == 0 && gucFailures.get() == 0 &&
            success.get() > 0 && prepares.get() > 0;
        log("RESULT: %s success=%d failures=%d prepares=%d rw_switches=%d rw_switch_failures=%d heartbeats=%d heartbeat_failures=%d guc_ops=%d guc_failures=%d finished_at=%s",
            passed ? "PASS" : "FAIL",
            success.get(), failures.get(), prepares.get(), rwSwitches.get(), rwSwitchFailures.get(),
            heartbeats.get(), heartbeatFailures.get(), gucOps.get(), gucFailures.get(), Instant.now().toString());
        if (!passed) {
            System.exit(1);
        }
    }

    private static void runLongClient(int clientId, List<String> sharedTemplates, List<String> privateTemplates, Random random, Config config, AtomicBoolean stop,
                                      AtomicLong success, AtomicLong failures, AtomicLong prepares, AtomicLong rwSwitches, AtomicLong rwSwitchFailures,
                                      AtomicLong heartbeats, AtomicLong heartbeatFailures, AtomicLong gucOps, AtomicLong gucFailures, CountDownLatch latch) {
        try (Connection conn = openConnection()) {
            while (!stop.get()) {
                executeOnce(conn, sharedTemplates, privateTemplates, random, success, failures, prepares, clientId, "long");
                runPeriodicCases(conn, config, prepares.get(), success, failures, rwSwitches, rwSwitchFailures, heartbeats, heartbeatFailures, gucOps, gucFailures, clientId, "long");
            }
        } catch (SQLException e) {
            failures.incrementAndGet();
            log("ERROR client_type=long client_id=%d message=%s", clientId, sanitize(e.getMessage()));
        } finally {
            latch.countDown();
        }
    }

    private static void runShortClient(int clientId, List<String> sharedTemplates, List<String> privateTemplates, Random random, Config config,
                                       AtomicBoolean stop, AtomicLong success, AtomicLong failures, AtomicLong prepares, AtomicLong rwSwitches, AtomicLong rwSwitchFailures,
                                       AtomicLong heartbeats, AtomicLong heartbeatFailures, AtomicLong gucOps, AtomicLong gucFailures,
                                       CountDownLatch latch) {
        try {
            while (!stop.get()) {
                try (Connection conn = openConnection()) {
                    for (int i = 0; i < config.shortBatch && !stop.get(); i++) {
                        executeOnce(conn, sharedTemplates, privateTemplates, random, success, failures, prepares, clientId, "short");
                        runPeriodicCases(conn, config, prepares.get(), success, failures, rwSwitches, rwSwitchFailures, heartbeats, heartbeatFailures, gucOps, gucFailures, clientId, "short");
                    }
                }
                if (!stop.get() && config.shortIdleMs > 0) {
                    Thread.sleep(config.shortIdleMs);
                }
            }
        } catch (SQLException e) {
            failures.incrementAndGet();
            log("ERROR client_type=short client_id=%d message=%s", clientId, sanitize(e.getMessage()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            latch.countDown();
        }
    }

    private static void runPeriodicCases(Connection conn, Config config, long opCount, AtomicLong success, AtomicLong failures,
                                         AtomicLong rwSwitches, AtomicLong rwSwitchFailures,
                                         AtomicLong heartbeats, AtomicLong heartbeatFailures,
                                         AtomicLong gucOps, AtomicLong gucFailures,
                                         int clientId, String clientType) {
        maybeRunHeartbeat(conn, config, opCount, success, failures, heartbeats, heartbeatFailures, clientId, clientType);
        maybeRunGucCase(conn, config, opCount, success, failures, gucOps, gucFailures, clientId, clientType);
        maybeRunRwSwitch(conn, config, opCount, success, failures, rwSwitches, rwSwitchFailures, clientId, clientType);
    }

    private static void maybeRunRwSwitch(Connection conn, Config config, long opCount, AtomicLong success, AtomicLong failures,
                                         AtomicLong rwSwitches, AtomicLong rwSwitchFailures, int clientId, String clientType) {
        if (!config.rwSwitchEnabled || config.rwSwitchIntervalOps <= 0 || opCount <= 0) {
            return;
        }
        if (opCount % config.rwSwitchIntervalOps != 0) {
            return;
        }
        try {
            runRwSwitchCase(conn, clientId, clientType, opCount);
            rwSwitches.incrementAndGet();
            success.addAndGet(4);
        } catch (SQLException e) {
            failures.incrementAndGet();
            rwSwitchFailures.incrementAndGet();
            log("ERROR client_type=%s client_id=%d rw_switch=true op=%d message=%s",
                clientType, clientId, opCount, sanitize(e.getMessage()));
        }
    }

    private static void maybeRunHeartbeat(Connection conn, Config config, long opCount, AtomicLong success, AtomicLong failures,
                                          AtomicLong heartbeats, AtomicLong heartbeatFailures, int clientId, String clientType) {
        if (!config.heartbeatEnabled || config.heartbeatIntervalOps <= 0 || opCount <= 0) {
            return;
        }
        if (opCount % config.heartbeatIntervalOps != 0) {
            return;
        }
        try {
            executeHeartbeat(conn);
            heartbeats.incrementAndGet();
            success.incrementAndGet();
        } catch (SQLException e) {
            failures.incrementAndGet();
            heartbeatFailures.incrementAndGet();
            log("ERROR client_type=%s client_id=%d heartbeat=true op=%d message=%s",
                clientType, clientId, opCount, sanitize(e.getMessage()));
        }
    }

    private static void maybeRunGucCase(Connection conn, Config config, long opCount, AtomicLong success, AtomicLong failures,
                                        AtomicLong gucOps, AtomicLong gucFailures, int clientId, String clientType) {
        if (!config.gucEnabled || config.gucIntervalOps <= 0 || opCount <= 0) {
            return;
        }
        if (opCount % config.gucIntervalOps != 0) {
            return;
        }
        try {
            runGucCase(conn, config, clientId, clientType, opCount);
            gucOps.incrementAndGet();
            success.addAndGet(16);
        } catch (SQLException e) {
            failures.incrementAndGet();
            gucFailures.incrementAndGet();
            log("ERROR client_type=%s client_id=%d guc=true op=%d message=%s",
                clientType, clientId, opCount, sanitize(e.getMessage()));
        }
    }

    private static void executeHeartbeat(Connection conn) throws SQLException {
        executeScalar(conn, "SELECT 10086");
    }

    private static void runGucCase(Connection conn, Config config, int clientId, String clientType, long opCount) throws SQLException {
        long gucSlot = config.gucDistinctCount > 0 ? Math.floorMod(opCount + clientId, config.gucDistinctCount) : opCount;
        String appName = "stable_" + clientType + "_" + clientId + "_" + gucSlot;
        String timeZone = (opCount % 2 == 0) ? "UTC" : "GMT";

        executeStatement(conn, "SET application_name = '" + appName + "'");
        executeStatement(conn, "SET TimeZone = '" + timeZone + "'");
        executeStatement(conn, "SET extra_float_digits = -2");
        executeStatement(conn, "SET enable_seqscan = off");
        executeScalar(conn, "SHOW application_name");
        executeScalar(conn, "SHOW TimeZone");
        executeScalar(conn, "SHOW extra_float_digits");
        executeScalar(conn, "SHOW enable_seqscan");
        executeStatement(conn, "RESET enable_seqscan");
        executeStatement(conn, "RESET extra_float_digits");
        executeStatement(conn, "RESET application_name");
        executeStatement(conn, "RESET TimeZone");
        executeStatement(conn, "RESET ALL");
    }

    private static void runRwSwitchCase(Connection conn, int clientId, String clientType, long opCount) throws SQLException {
        int marker = (int) (Math.abs((clientType + clientId + ":" + opCount).hashCode()) % 1_000_000);
        boolean oldAutoCommit = conn.getAutoCommit();
        boolean oldReadOnly = conn.isReadOnly();
        try {
            conn.setAutoCommit(false);
            conn.setReadOnly(true);
            executeScalar(conn, "SELECT ?::int", marker);
            executeScalar(conn, "SELECT pg_is_in_recovery()::text");
            conn.commit();

            conn.setReadOnly(false);
            conn.setAutoCommit(false);
            executeUpdate(conn, "INSERT INTO test_prepare(data) VALUES (?)", "rw-switch-" + clientType + "-" + clientId + "-" + opCount);
            executeUpdate(conn, "DELETE FROM test_prepare WHERE data = ?", "rw-switch-" + clientType + "-" + clientId + "-" + opCount);
            conn.commit();
        } catch (SQLException e) {
            rollbackQuietly(conn);
            throw e;
        } finally {
            conn.setReadOnly(oldReadOnly);
            conn.setAutoCommit(oldAutoCommit);
        }
    }

    private static void executeScalar(Connection conn, String sql, Object... args) throws SQLException {
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            bindArgs(pstmt, args);
            try (ResultSet rs = pstmt.executeQuery()) {
                while (rs.next()) {
                    rs.getString(1);
                }
            }
        }
    }

    private static void executeUpdate(Connection conn, String sql, Object... args) throws SQLException {
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            bindArgs(pstmt, args);
            pstmt.executeUpdate();
        }
    }

    private static void executeStatement(Connection conn, String sql) throws SQLException {
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            pstmt.execute();
        }
    }

    private static void bindArgs(PreparedStatement pstmt, Object... args) throws SQLException {
        for (int i = 0; i < args.length; i++) {
            Object value = args[i];
            if (value instanceof Integer) {
                pstmt.setInt(i + 1, (Integer) value);
            } else {
                pstmt.setString(i + 1, String.valueOf(value));
            }
        }
    }

    private static void rollbackQuietly(Connection conn) {
        try {
            conn.rollback();
        } catch (SQLException ignored) {
        }
    }

    private static void executeOnce(Connection conn, List<String> sharedTemplates, List<String> privateTemplates, Random random, AtomicLong success,
                                    AtomicLong failures, AtomicLong prepares, int clientId, String clientType) {
        String sql = pickSql(sharedTemplates, privateTemplates, random);
        try (PreparedStatement pstmt = conn.prepareStatement(sql)) {
            bindRandom(pstmt, sql, random);
            prepares.incrementAndGet();
            pstmt.execute();
            success.incrementAndGet();
        } catch (SQLException e) {
            failures.incrementAndGet();
            log("ERROR client_type=%s client_id=%d sql_hash=%d message=%s",
                clientType, clientId, sql.hashCode(), sanitize(e.getMessage()));
        }
    }

    private static void bindRandom(PreparedStatement pstmt, String sql, Random random) throws SQLException {
        int paramCount = countQuestionMarks(sql);
        for (int i = 1; i <= paramCount; i++) {
            pstmt.setInt(i, random.nextInt(1_000_000));
        }
    }

    private static int countQuestionMarks(String sql) {
        int count = 0;
        for (int i = 0; i < sql.length(); i++) {
            if (sql.charAt(i) == '?') {
                count++;
            }
        }
        return count;
    }

    private static String pickSql(List<String> sharedTemplates, List<String> privateTemplates, Random random) {
        int sharedSize = sharedTemplates.size();
        int privateSize = privateTemplates.size();
        int total = sharedSize + privateSize;
        if (total <= 0) {
            throw new IllegalArgumentException("at least one SQL template is required");
        }
        int index = random.nextInt(total);
        if (index < sharedSize) {
            return sharedTemplates.get(index);
        }
        return privateTemplates.get(index - sharedSize);
    }

    private static List<String> buildSharedSqlTemplates(int count) {
        return buildSqlTemplates(count, 0, "shared");
    }

    private static List<String> buildPrivateSqlTemplates(int clientId, String clientType, int count) {
        return buildSqlTemplates(count, clientId, clientType);
    }

    private static List<String> buildSqlTemplates(int count, int clientId, String scope) {
        List<String> templates = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            int type = i % 4;
            String tag = String.format(Locale.ROOT, "%s-c%03d-%04d", scope, clientId, i);
            switch (type) {
                case 0:
                    templates.add(String.format(Locale.ROOT,
                        "SELECT id, data FROM table_test WHERE id = ? AND (%d = %d OR id >= ?) /* %s */", i, i, i + 1, tag));
                    break;
                case 1:
                    templates.add(String.format(Locale.ROOT,
                        "INSERT INTO test_prepare(data) VALUES (CAST(? AS TEXT) || '-tpl-%s')", tag));
                    break;
                case 2:
                    templates.add(String.format(Locale.ROOT,
                        "UPDATE test_prepare SET data = CAST(? AS TEXT) || '-u-%s' WHERE id = ?", tag));
                    break;
                default:
                    templates.add(String.format(Locale.ROOT,
                        "DELETE FROM test_prepare WHERE id = ? AND (%d = %d OR id >= 0) /* %s */", i, i, tag));
                    break;
            }
        }
        return templates;
    }

    private static Connection openConnection() throws SQLException {
        Properties props = new Properties();
        props.setProperty("user", JDBC_USER);
        props.setProperty("password", JDBC_PASSWORD);
        props.setProperty("prepareThreshold", JDBC_PREPARE_THRESHOLD);
        props.setProperty("preparedStatementCacheQueries", JDBC_PS_CACHE_QUERIES);
        return DriverManager.getConnection(JDBC_URL, props);
    }

    private static String sanitize(String text) {
        if (text == null) {
            return "";
        }
        return text.replace('\n', ' ').replace('\r', ' ');
    }

    private static void log(String format, Object... args) {
        System.out.printf((Instant.now().toString() + " " + format + "%n"), args);
        System.out.flush();
    }

    private static final class Config {
        final String workloadName;
        final long durationSeconds;
        final int longClients;
        final int shortClients;
        final int sharedSqlCount;
        final int privateSqlCount;
        final int shortBatch;
        final int shortIdleMs;
        final boolean rwSwitchEnabled;
        final int rwSwitchIntervalOps;
        final boolean heartbeatEnabled;
        final int heartbeatIntervalOps;
        final boolean gucEnabled;
        final int gucIntervalOps;
        final int gucDistinctCount;

        private Config(String workloadName, long durationSeconds, int longClients, int shortClients, int sharedSqlCount, int privateSqlCount, int shortBatch, int shortIdleMs, boolean rwSwitchEnabled, int rwSwitchIntervalOps, boolean heartbeatEnabled, int heartbeatIntervalOps, boolean gucEnabled, int gucIntervalOps, int gucDistinctCount) {
            this.workloadName = workloadName;
            this.durationSeconds = durationSeconds;
            this.longClients = longClients;
            this.shortClients = shortClients;
            this.sharedSqlCount = sharedSqlCount;
            this.privateSqlCount = privateSqlCount;
            this.shortBatch = shortBatch;
            this.shortIdleMs = shortIdleMs;
            this.rwSwitchEnabled = rwSwitchEnabled;
            this.rwSwitchIntervalOps = rwSwitchIntervalOps;
            this.heartbeatEnabled = heartbeatEnabled;
            this.heartbeatIntervalOps = heartbeatIntervalOps;
            this.gucEnabled = gucEnabled;
            this.gucIntervalOps = gucIntervalOps;
            this.gucDistinctCount = gucDistinctCount;
        }

        static Config parse(String[] args) {
            String workloadName = "prepared_leak";
            long durationSeconds = 7200L;
            int longClients = 10;
            int shortClients = 4;
            int sharedSqlCount = 2000;
            int privateSqlCount = 0;
            int shortBatch = 10;
            int shortIdleMs = 0;
            boolean rwSwitchEnabled = false;
            int rwSwitchIntervalOps = 0;
            boolean heartbeatEnabled = false;
            int heartbeatIntervalOps = 0;
            boolean gucEnabled = false;
            int gucIntervalOps = 0;
            int gucDistinctCount = 200;

            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--workload":
                        workloadName = args[++i];
                        break;
                    case "--duration":
                        durationSeconds = Long.parseLong(args[++i]);
                        break;
                    case "--long-clients":
                        longClients = Integer.parseInt(args[++i]);
                        break;
                    case "--short-clients":
                        shortClients = Integer.parseInt(args[++i]);
                        break;
                    case "--shared-sql-count":
                        sharedSqlCount = Integer.parseInt(args[++i]);
                        break;
                    case "--private-sql-count":
                        privateSqlCount = Integer.parseInt(args[++i]);
                        break;
                    case "--short-batch":
                        shortBatch = Integer.parseInt(args[++i]);
                        break;
                    case "--short-idle-ms":
                        shortIdleMs = Integer.parseInt(args[++i]);
                        break;
                    case "--rw-switch-enabled":
                        rwSwitchEnabled = Boolean.parseBoolean(args[++i]);
                        break;
                    case "--rw-switch-interval-ops":
                        rwSwitchIntervalOps = Integer.parseInt(args[++i]);
                        break;
                    case "--heartbeat-enabled":
                        heartbeatEnabled = Boolean.parseBoolean(args[++i]);
                        break;
                    case "--heartbeat-interval-ops":
                        heartbeatIntervalOps = Integer.parseInt(args[++i]);
                        break;
                    case "--guc-enabled":
                        gucEnabled = Boolean.parseBoolean(args[++i]);
                        break;
                    case "--guc-interval-ops":
                        gucIntervalOps = Integer.parseInt(args[++i]);
                        break;
                    case "--guc-distinct-count":
                        gucDistinctCount = Integer.parseInt(args[++i]);
                        break;
                    default:
                        throw new IllegalArgumentException("unknown argument: " + args[i]);
                }
            }

            return new Config(workloadName, durationSeconds, longClients, shortClients, sharedSqlCount, privateSqlCount, shortBatch, shortIdleMs, rwSwitchEnabled, rwSwitchIntervalOps, heartbeatEnabled, heartbeatIntervalOps, gucEnabled, gucIntervalOps, gucDistinctCount);
        }
    }
}
