paths = [
    "/home/postgres/fly_dev/pgcluster/pgclusterlib/runtime.py",
    "/home/postgres/fly_dev/postgresql_for_fbase_dev/pgcluster/pgclusterlib/runtime.py"
]

old = """    def stop_instance(self, name):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        return self.executor.run(
            [install["home"] + "/bin/pg_ctl", "stop", "-D", instance["data_dir"], "-m", "fast", "-w"], host=host
        )"""

new = """    def stop_instance(self, name, mode="fast", timeout=8):
        instance = self.config.instance(name)
        host = self._host(instance)
        install = instance["installation_config"]
        try:
            return self.executor.run(
                [install["home"] + "/bin/pg_ctl", "stop", "-D", instance["data_dir"], "-m", mode, "-w", "-t", str(timeout)], host=host
            )
        except Exception:
            if mode != "immediate":
                return self.executor.run(
                    [install["home"] + "/bin/pg_ctl", "stop", "-D", instance["data_dir"], "-m", "immediate", "-w", "-t", "5"], host=host
                )
            raise"""

for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        c = f.read()
    if old in c:
        c = c.replace(old, new)
        with open(p, "w", encoding="utf-8") as f:
            f.write(c)
        print("Successfully patched:", p)
    else:
        print("Pattern already patched or not found in:", p)
