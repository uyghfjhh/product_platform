#!/usr/bin/env python3
"""Send one Extended Query cycle per SQL and record every server response."""

import json
import sys

from platform_regress.clients.pgwire import connect, extended_execute


def execute(sock, variant, label, sql):
    record = extended_execute(sock, sql, variant=variant, step=label)
    print("STEP_JSON=" + json.dumps(record, ensure_ascii=False), flush=True)


def run_variant(port, variant, extra_failure):
    with connect("127.0.0.1", port, "mmr_group", "postgres") as sock:
        steps = (("begin", "BEGIN"), ("savepoint", "SAVEPOINT s4"),
                 ("division", "SELECT 1/0"))
        for label, sql in steps:
            execute(sock, variant, label, sql)
        if extra_failure:
            execute(sock, variant, "aborted_select", "SELECT 1")
        execute(sock, variant, "rollback_to", "ROLLBACK TO SAVEPOINT s4")
        execute(sock, variant, "recovery_select", "SELECT 9")
        execute(sock, variant, "cleanup", "ROLLBACK")


if __name__ == "__main__":
    port = int(sys.argv[1])
    run_variant(port, "direct_recovery", False)
    run_variant(port, "after_local_25p02", True)
