"""Deterministic two-node transaction timing for MMR conflict cases."""


def parallel_transactions(psql, first_port, first_sql, second_port, second_sql,
                          first_wait, second_wait, lock_base=390000000):
    """Run both DML statements before either transaction commits.

    ``pg_advisory_xact_lock`` is used only as an observable ready signal.  A
    failed ``pg_try_advisory_xact_lock`` proves the corresponding DML has run
    and its transaction remains open; it is not used to serialize table work.
    """
    first_lock, second_lock = lock_base + 1, lock_base + 2
    first_tx = ("BEGIN; %s; SELECT pg_advisory_xact_lock(%s); "
                "SELECT pg_sleep(%s); COMMIT" % (first_sql, first_lock, first_wait))
    second_tx = ("BEGIN; %s; SELECT pg_advisory_xact_lock(%s); "
                 "SELECT pg_sleep(%s); COMMIT" % (second_sql, second_lock, second_wait))
    return (
        "first_log=/tmp/fbase_regress_conflict_first.log; second_log=/tmp/fbase_regress_conflict_second.log; "
        "rm -f \"$first_log\" \"$second_log\"; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$first_log\" 2>&1 & first_pid=$!; "
        "first_ready=; for attempt in $(seq 1 100); do "
        "first_ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$first_ready\" = f && break; sleep 0.1; done; "
        "test \"$first_ready\" = f || { cat \"$first_log\"; exit 1; }; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$second_log\" 2>&1 & second_pid=$!; "
        "second_ready=; for attempt in $(seq 1 100); do "
        "second_ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$second_ready\" = f && break; sleep 0.1; done; "
        "test \"$second_ready\" = f || { cat \"$first_log\" \"$second_log\"; exit 1; }; "
        "first_status=0; second_status=0; wait \"$first_pid\" || first_status=$?; wait \"$second_pid\" || second_status=$?; "
        "cat \"$first_log\" \"$second_log\"; rm -f \"$first_log\" \"$second_log\"; "
        "test \"$first_status\" = 0 -a \"$second_status\" = 0" %
        (psql, first_port, first_tx, psql, first_port,
         "SELECT pg_try_advisory_xact_lock(%s)" % first_lock,
         psql, second_port, second_tx, psql, second_port,
         "SELECT pg_try_advisory_xact_lock(%s)" % second_lock))
