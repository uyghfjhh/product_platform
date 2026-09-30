"""PostgreSQL timestamp-window logs and closed-file archives."""

import shlex


def postgres_log_window_script(
    postgres_dir, port, user, since, label, *, host="127.0.0.1", patterns=()
):
    """Return a log query that only emits records written during this run.

    PostgreSQL appends to a long-lived log file.  Filtering files by mtime and
    then reading their full tails makes errors from an earlier run look new.
    Keep continuation lines with their timestamped record, but only emit a
    record after the run start timestamp.
    """
    return """set -eu
PG={pg}/bin/psql
data_dir=$($PG -h {host} -p {port} -U {user} -d postgres -At -c 'show data_directory')
log_dir=$($PG -h {host} -p {port} -U {user} -d postgres -At -c 'show log_directory')
case \"$log_dir\" in /*) ;; *) log_dir=\"$data_dir/$log_dir\" ;; esac
run_start=$(date -d '@{since}' '+%Y-%m-%d %H:%M:%S')
printf 'NODE={label} PORT={port} DATA_DIR=%s LOG_DIR=%s RUN_START=%s\\n' \"$data_dir\" \"$log_dir\" \"$run_start\"
for file in $(find \"$log_dir\" -maxdepth 1 -type f -newermt '@{since}' -print | sort); do
  printf '\\n=== FILE: %s ===\\n' \"$file\"
  tail -n 800 \"$file\" | awk -v start=\"$run_start\" '
    /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ {{
      keep = substr($0, 1, 19) >= start
    }}
    keep {{ print }}
  ' | grep -Ei {filter_pattern} || true
done
""".format(
        pg=shlex.quote(str(postgres_dir)),
        port=int(port),
        user=shlex.quote(str(user)),
        since=int(since),
        label=label,
        host=host,
        filter_pattern=shlex.quote("|".join(patterns) or "."),
    )


def postgres_log_archive_script(
    postgres_dir,
    port,
    user,
    since,
    label,
    *,
    host="127.0.0.1",
    archive_subdir="archive",
    archive_prefix="run",
    remove_sources=False,
):
    """Build a remote archive command that never moves PostgreSQL's open log."""
    return """set -eu
PG={pg}/bin/psql
data_dir=$($PG -h {host} -p {port} -U {user} -d postgres -At -c 'show data_directory')
log_dir=$($PG -h {host} -p {port} -U {user} -d postgres -At -c 'show log_directory')
case \"$log_dir\" in /*) ;; *) log_dir=\"$data_dir/$log_dir\" ;; esac
archive_dir=\"$log_dir/{archive_subdir}\"
mkdir -p \"$archive_dir\"
command -v lsof >/dev/null 2>&1 || {{ echo 'ERROR: lsof is required to protect open PostgreSQL logs' >&2; exit 2; }}
open_files=$(lsof -F n +d \"$log_dir\" 2>/dev/null | sed -n 's/^n//p')
set --
archived=0
skipped_open=0
for pattern in '*.csv' '*.log'; do
  for file in \"$log_dir\"/$pattern; do
    [ -f \"$file\" ] || continue
    modified=$(stat -c %Y \"$file\" 2>/dev/null || printf '0')
    [ \"$modified\" -ge {since} ] || continue
    if printf '%s\\n' \"$open_files\" | grep -Fx \"$file\" >/dev/null 2>&1; then
      skipped_open=$((skipped_open + 1))
      continue
    fi
    set -- \"$@\" \"$file\"
    archived=$((archived + 1))
  done
done
archive_path=''
if [ \"$archived\" -gt 0 ]; then
  archive_path=\"$archive_dir/{archive_prefix}_{label}_$(date +%Y%m%d_%H%M%S).tar.gz\"
  tar -czf \"$archive_path\" {remove_flag} \"$@\"
fi
printf 'NODE={label} PORT={port} LOG_DIR=%s\\n' \"$log_dir\"
printf 'ARCHIVED_FILES=%s\\n' \"$archived\"
printf 'SKIPPED_OPEN_FILES=%s\\n' \"$skipped_open\"
printf 'ARCHIVE=%s\\n' \"$archive_path\"
""".format(
        pg=shlex.quote(str(postgres_dir)),
        port=int(port),
        user=shlex.quote(str(user)),
        since=int(since),
        label=label,
        host=host,
        archive_subdir=archive_subdir,
        archive_prefix=archive_prefix,
        remove_flag="--remove-files" if remove_sources else "",
    )
