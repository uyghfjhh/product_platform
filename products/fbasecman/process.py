"""Reusable fbasecman process lifecycle."""

import time
from pathlib import Path

from framework.clients.psql import build_psql_command
from platform_regress.execution.daemon import ManagedDaemon, ManagedDaemonError

FbasecmanProcessError = ManagedDaemonError


class FbasecmanProcess(ManagedDaemon):
    def __init__(self, binary, postgres_dir, listen_port, prom_port, pid_file,
                 locks_dir, product_log, logs_dir, execute, trace,
                 port_is_free, sleep=time.sleep):
        self.postgres_dir = str(postgres_dir)
        self.prom_port = int(prom_port)
        self.locks_dir = Path(locks_dir)
        super().__init__(
            name="fbasecman",
            binary=binary,
            listen_port=listen_port,
            pid_file=pid_file,
            product_log=product_log,
            logs_dir=logs_dir,
            execute=execute,
            trace=trace,
            port_is_free=port_is_free,
            ready_probe=self._console_ready_command,
            ready_label="console",
            forensics_dirs=(self.locks_dir,),
            sleep=sleep,
        )

    def _console_ready_command(self):
        return build_psql_command(
            self.postgres_dir, "localhost", self.listen_port, "admin", "console",
            "show global_prepared_statements_stats;",
            footer=False, output_format="unaligned",
        )

    def config_replacements(self, log_level):
        return [
            ('pid_file "/tmp/fbasecman.pid"', 'pid_file "%s"' % self.pid_file),
            ('locks_dir "/tmp/odyssey"', 'locks_dir "%s"' % self.locks_dir),
            ('ports "17432"', 'ports "%s"' % self.listen_port),
            ('promhttp_server_port 7777', 'promhttp_server_port %s' % self.prom_port),
            ('log_file "/home/postgres/fly_dev/fbasecman_dev_autotest/test/fbasecman/test_logs/extend_query/mmr_hint_pool.log"', 'log_file "%s"' % self.product_log),
            ('log_file "/home/postgres/fly_dev/fbasecman_dev_autotest/test/fbasecman/test_logs/extend_query/rep_hint_pool.log"', 'log_file "%s"' % self.product_log),
            ('log_min_messages "info"', 'log_min_messages "%s"' % log_level),
        ]
