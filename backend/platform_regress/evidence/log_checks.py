"""Cross-suite checks for forbidden log patterns."""


def find_forbidden_log_patterns(paths, patterns):
    found = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            if pattern in text:
                found.append({"pattern": pattern, "path": str(path)})
    return found
