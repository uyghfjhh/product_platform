"""PostgreSQL database node lifecycle and fault injection controls."""

import time
from framework.execution.shell import LoggedShellRunner


class ClusterOpsError(RuntimeError):
    pass


class NodeController(object):
    """Controls PostgreSQL instances (A0, A1, B0, B1) on the database host."""

    def __init__(self, env, logs_dir):
        self.env = env
        self.logs_dir = logs_dir
        self.shell_runner = LoggedShellRunner(self.logs_dir, verbose=False)
        self.db_cfg = self.env.config["database"]
        self.data_root = self.db_cfg.get("mmr_data_root", self.db_cfg["mmr_postgres_dir"])
        self.pg_dir = self.db_cfg["mmr_postgres_dir"]
        self.host = self.db_cfg["mmr_host"]
        self.user = self.db_cfg["mmr_pg_user"]
        self.repl_user = self.db_cfg.get("mmr_repl_user", "replicator")
        ports = self.db_cfg["ports"]

        # Mapping logical names to (pgdata_name, port)
        self.nodes = {
            "A0": ("test_mmr1", ports["mmr1"]),
            "A1": ("test_mmr1_s1", ports["mmr1_standby1"]),
            "B0": ("test_mmr2", ports["mmr2"]),
            "B1": ("test_mmr2_s1", ports["mmr2_standby1"]),
            "test_mmr1": ("test_mmr1", ports["mmr1"]),
            "test_mmr1_s1": ("test_mmr1_s1", ports["mmr1_standby1"]),
            "test_mmr2": ("test_mmr2", ports["mmr2"]),
            "test_mmr2_s1": ("test_mmr2_s1", ports["mmr2_standby1"]),
        }

        self.last_command = ""
        self.last_output = ""
        self.last_rc = 0
        self.last_operation_transcript = ""
        self.operation_history = []

    def _pgdata_path(self, node_key):
        pgdata_name, _ = self.nodes[node_key]
        return "%s/%s" % (self.data_root, pgdata_name)

    def _run_cmd(self, cmd, log_name):
        full_ssh_cmd = "ssh -F /dev/null %s@%s '%s'" % (self.user, self.host, cmd)
        self.last_command = full_ssh_cmd
        res = self.shell_runner.run_remote(self.user, self.host, cmd, log_name, check=False)
        self.last_output = (res.stdout + res.stderr).strip()
        self.last_rc = res.returncode
        self.last_operation_transcript = self._format_result(full_ssh_cmd, res.returncode, self.last_output)
        self.operation_history.append(self.last_operation_transcript)
        return res.returncode, self.last_output

    @staticmethod
    def _format_result(command, returncode, output):
        return "%s\n返回码: %s\n输出:\n%s" % (
            command, returncode, output.strip() if output.strip() else "<无输出>",
        )

    def is_running(self, node_key):
        pgdata = self._pgdata_path(node_key)
        cmd = '%s/bin/pg_ctl -D "%s" status' % (self.pg_dir, pgdata)
        rc, out = self._run_cmd(cmd, "pg_status_%s.log" % node_key)
        return "server is running" in out

    def stop_node(self, node_key, immediate=True):
        pgdata = self._pgdata_path(node_key)
        mode = "-m immediate" if immediate else "-m fast"
        cmd = (
            'if %s/bin/pg_ctl -D "%s" status >/dev/null 2>&1; then '
            '%s/bin/pg_ctl -D "%s" %s stop; '
            'else echo "already stopped"; fi'
            % (self.pg_dir, pgdata, self.pg_dir, pgdata, mode)
        )
        rc, out = self._run_cmd(cmd, "pg_stop_%s.log" % node_key)
        # Give a moment for socket release
        time.sleep(0.5)
        return rc == 0

    def start_node(self, node_key, timeout=15):
        pgdata = self._pgdata_path(node_key)
        cmd = (
            'if %s/bin/pg_ctl -D "%s" status >/dev/null 2>&1; then echo "already running"; exit 0; fi; '
            '%s/bin/pg_ctl -D "%s" -l "%s/logfile" -w -t %d start'
            % (self.pg_dir, pgdata, self.pg_dir, pgdata, pgdata, timeout)
        )
        rc, out = self._run_cmd(cmd, "pg_start_%s.log" % node_key)
        time.sleep(0.5)
        return rc == 0

    def promote_node(self, node_key):
        pgdata = self._pgdata_path(node_key)
        cmd = '%s/bin/pg_ctl -D "%s" promote' % (self.pg_dir, pgdata)
        rc, out = self._run_cmd(cmd, "pg_promote_%s.log" % node_key)
        time.sleep(1)
        return rc == 0

    def rebuild_replica(self, standby_key, primary_key, app_name=None):
        """Rebuild standby from primary using pg_basebackup."""
        st_pgdata = self._pgdata_path(standby_key)
        _, pr_port = self.nodes[primary_key]
        _, st_port = self.nodes[standby_key]
        if not app_name:
            if standby_key in ("A0", "test_mmr1"):
                app_name = "test_mmr1"
            elif standby_key in ("A1", "test_mmr1_s1"):
                app_name = "pg_240"
            elif standby_key in ("B0", "test_mmr2"):
                app_name = "test_mmr2"
            elif standby_key in ("B1", "test_mmr2_s1"):
                app_name = "pg_250"
        self.stop_node(standby_key, immediate=True)
        stop_transcript = self.last_operation_transcript

        cmd = (
            'rm -rf "%s" && '
            '%s/bin/pg_basebackup -h "%s" -p %d -U "%s" -D "%s" -Fp -Xs -R && '
            'sed -i "s/target_session_attrs=any/target_session_attrs=any application_name=%s/" "%s/postgresql.auto.conf" && '
            'echo "port=%d" >>"%s/postgresql.conf"'
            % (st_pgdata, self.pg_dir, self.host, pr_port, self.repl_user, st_pgdata, app_name, st_pgdata, st_port, st_pgdata)
        )
        rc, out = self._run_cmd(cmd, "pg_rebuild_%s.log" % standby_key)
        rebuild_transcript = self.last_operation_transcript
        if rc != 0:
            raise ClusterOpsError("pg_basebackup rebuild for %s failed: %s" % (standby_key, out))
        started = self.start_node(standby_key)
        start_transcript = self.last_operation_transcript
        self.last_operation_transcript = "\n\n".join(
            (stop_transcript, rebuild_transcript, start_transcript)
        )
        return started

    def is_in_recovery(self, node_key):
        """Check if node is currently in recovery (standby)."""
        _, port = self.nodes[node_key]
        cmd = '%s/bin/psql -h 127.0.0.1 -p %d -U %s -d postgres -tAc "SELECT pg_is_in_recovery();"' % (
            self.pg_dir, port, self.user
        )
        rc, out = self._run_cmd(cmd, "pg_recovery_%s.log" % node_key)
        return rc == 0 and "t" in out.strip().lower()

    def ensure_all_running(self, heal_standby=False):
        """Ensure core nodes A0, A1, B0, B1 are running."""
        for key in ("A0", "A1", "B0", "B1"):
            if not self.is_running(key):
                self.start_node(key)
        if heal_standby:
            self.heal_cluster(verify_replication=True)

    def heal_cluster(self, verify_replication=True):
        """Comprehensive healing: ensure running and restore broken standby replicas."""
        restarted = []
        rebuilt = []
        errors = []

        for key in ("A0", "A1", "B0", "B1"):
            if not self.is_running(key):
                ok = self.start_node(key)
                if ok:
                    restarted.append(key)
                else:
                    errors.append("启动节点 %s 失败" % key)

        if verify_replication:
            # Check A1 is standby of A0
            try:
                if self.is_running("A1") and not self.is_in_recovery("A1"):
                    self.rebuild_replica("A1", "A0")
                    rebuilt.append("A1")
            except Exception as exc:
                errors.append("检查/重构 A1 副本失败: %s" % exc)

            # Check B1 is standby of B0
            try:
                if self.is_running("B1") and not self.is_in_recovery("B1"):
                    self.rebuild_replica("B1", "B0")
                    rebuilt.append("B1")
            except Exception as exc:
                errors.append("检查/重构 B1 副本失败: %s" % exc)

        return {
            "healthy": len(errors) == 0,
            "restarted": restarted,
            "rebuilt": rebuilt,
            "errors": errors,
        }

