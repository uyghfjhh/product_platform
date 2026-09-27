import re

paths = [
    "/home/postgres/fly_dev/pgcluster/pgclusterlib/runtime.py",
    "/home/postgres/fly_dev/postgresql_for_fbase_dev/pgcluster/pgclusterlib/runtime.py"
]

new_code = '''    def stop_instance(self, name, mode="fast", timeout=6):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        data_dir = instance["data_dir"]
        pg_ctl = install["home"] + "/bin/pg_ctl"
        try:
            return self.executor.run(
                [pg_ctl, "stop", "-D", data_dir, "-m", mode, "-w", "-t", str(timeout)],
                host=host,
            )
        except Exception:
            # 1. Fallback to immediate mode if fast mode hangs/times out
            try:
                return self.executor.run(
                    [pg_ctl, "stop", "-D", data_dir, "-m", "immediate", "-w", "-t", "4"],
                    host=host,
                )
            except Exception:
                pass

            # 2. Hard kill postmaster and lingering walsenders if still running
            pid_file = Path(data_dir) / "postmaster.pid"
            self.executor.run(
                ["pkill", "-9", "-f", f"postgres.*{data_dir}"],
                host=host,
                check=False,
            )
            time.sleep(0.5)
            if pid_file.is_file():
                try:
                    pid_file.unlink()
                except OSError:
                    pass
            return "已强制终止实例: %s" % name'''

for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()

    # Match def stop_instance up to def start_target
    pattern = r'    def stop_instance\(self, name.*?\n(?=    def start_target)'
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_code + "\n\n", content, flags=re.DOTALL)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print("Successfully updated stop_instance in:", p)
    else:
        print("Pattern not found in:", p)
