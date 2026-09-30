"""pgbench command construction and standard result metrics."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PgbenchRequest:
    binary: str
    host: str
    port: int
    user: str
    database: str
    sql_file: str
    clients: int = 1
    jobs: int = 1
    progress_seconds: int = 5
    connect_per_transaction: bool = False

    def argv(self):
        if (
            not 1 <= self.port <= 65535
            or min(self.clients, self.jobs, self.progress_seconds) <= 0
        ):
            raise ValueError("invalid pgbench parameters")
        command = [
            self.binary,
            "-n",
            "-P",
            str(self.progress_seconds),
            "-h",
            self.host,
            "-p",
            str(self.port),
            "-U",
            self.user,
            "-d",
            self.database,
            "-f",
            self.sql_file,
            "-c",
            str(self.clients),
            "-j",
            str(self.jobs),
        ]
        if self.connect_per_transaction:
            command.append("-C")
        return command


def parse_pgbench_result(text, returncode=0):
    def number(pattern, kind=int):
        match = re.search(pattern, text, re.I)
        return kind(match.group(1)) if match else 0

    transactions = number(r"number of transactions actually processed:\s*(\d+)")
    failures = number(r"number of failed transactions:\s*(\d+)")
    return {
        "ok": returncode == 0 and transactions > 0 and failures == 0,
        "transactions": transactions,
        "failures": failures,
        "returncode": returncode,
        "tps": number(r"tps\s*=\s*([\d.]+)", float),
        "latency_ms": number(r"latency average\s*=\s*([\d.]+)", float),
    }


def filtered_pgbench_command(argv):
    from pathlib import Path

    return ["bash", str(Path(__file__).parent / "assets/run_pgbench.sh"), *argv]
