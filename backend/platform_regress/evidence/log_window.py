"""Log-window primitives for action-scoped evidence collection."""

import os


class LogMark(object):
    def __init__(self, path, inode, size):
        self.path = str(path)
        self.inode = inode
        self.size = size

    def as_dict(self):
        return {"path": self.path, "inode": self.inode, "size": self.size}


class LocalLogWindow(object):
    """Capture only the log content produced after an action begins."""

    def mark(self, paths):
        marks = {}
        for path in paths:
            text_path = str(path)
            try:
                stat = os.stat(text_path)
                marks[text_path] = LogMark(text_path, stat.st_ino, stat.st_size)
            except OSError:
                marks[text_path] = LogMark(text_path, None, 0)
        return marks

    def collect(self, marks):
        captured = {}
        for path, mark in marks.items():
            try:
                stat = os.stat(path)
            except OSError:
                captured[path] = ""
                continue
            offset = mark.size if mark.inode == stat.st_ino and stat.st_size >= mark.size else 0
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                captured[path] = handle.read()
        return captured

    @staticmethod
    def matching_lines(captured, patterns):
        lowered = [pattern.lower() for pattern in patterns]
        result = []
        for path in sorted(captured):
            for raw_line in captured[path].splitlines():
                if any(pattern in raw_line.lower() for pattern in lowered):
                    result.append((path, raw_line))
        return result


def parse_remote_log_snapshot(text):
    """Parse path, inode and size lines emitted by the remote collector."""
    marks = {}
    for raw_line in text.splitlines():
        parts = raw_line.rstrip("\n").split("\t")
        if len(parts) != 3:
            continue
        path, inode, size = parts
        try:
            marks[path] = LogMark(path, int(inode), int(size))
        except ValueError:
            continue
    return marks


def remote_snapshot_script(log_dirs):
    """Build a shell snippet that lists remote log files with stable cursors."""
    quoted_dirs = " ".join("'%s'" % str(item).replace("'", "'\"'\"'") for item in log_dirs)
    return (
        "for dir in %s; do\n"
        "  [ -d \"$dir\" ] || continue\n"
        "  find \"$dir\" -type f -printf '%%p\\t%%i\\t%%s\\n'\n"
        "done"
    ) % quoted_dirs


def remote_collect_script(log_dirs, marks):
    """Build a collector that emits content added since a remote snapshot."""
    baseline = "\n".join(
        "%s\t%s\t%s" % (mark.path, mark.inode, mark.size)
        for _, mark in sorted(marks.items()) if mark.inode is not None
    )
    quoted_dirs = " ".join("'%s'" % str(item).replace("'", "'\"'\"'") for item in log_dirs)
    return (
        "declare -A old_size\n"
        "while IFS=$'\\t' read -r path inode size; do\n"
        "  [ -n \"$inode\" ] && old_size[$inode]=$size\n"
        "done <<'__LOG_WINDOW_BASELINE__'\n"
        "%s\n"
        "__LOG_WINDOW_BASELINE__\n"
        "for dir in %s; do\n"
        "  [ -d \"$dir\" ] || continue\n"
        "  while IFS=$'\\t' read -r path inode size; do\n"
        "    offset=${old_size[$inode]:-0}\n"
        "    [ \"$size\" -ge \"$offset\" ] || offset=0\n"
        "    [ \"$size\" -gt \"$offset\" ] || continue\n"
        "    echo \"===== LOG: $path =====\"\n"
        "    if [ \"$offset\" -gt 0 ]; then tail -c +$((offset + 1)) \"$path\"; else cat \"$path\"; fi\n"
        "  done < <(find \"$dir\" -type f -printf '%%p\\t%%i\\t%%s\\n')\n"
        "done"
    ) % (baseline, quoted_dirs)
