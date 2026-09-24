"""Product-neutral assertion helpers."""


def missing_markers(text, required_markers):
    return [marker for marker in required_markers if marker not in text]


def require_markers(text, required_markers, context, exception_type=AssertionError):
    missing = missing_markers(text, required_markers)
    if missing:
        raise exception_type("%s missing markers: %s" % (context, ", ".join(missing)))
    return list(required_markers)


def matching_rows(rows, column, contains=None, equals=None, case_sensitive=False,
                  minimum_columns=None):
    if contains is None and equals is None:
        raise ValueError("matching_rows requires contains or equals")
    required_columns = minimum_columns if minimum_columns is not None else column + 1
    matched = []
    for row in rows:
        if len(row) < required_columns or len(row) <= column:
            continue
        value = row[column].strip()
        expected_contains = contains
        expected_equals = equals
        if not case_sensitive:
            value = value.lower()
            expected_contains = contains.lower() if contains is not None else None
            expected_equals = equals.lower() if equals is not None else None
        if expected_contains is not None and expected_contains not in value:
            continue
        if expected_equals is not None and expected_equals != value:
            continue
        matched.append(row)
    return matched


def pipe_rows(rows):
    return ["|".join(row) for row in rows]


def stats_delta(before, after):
    delta = {}
    for key in sorted(set(before) | set(after)):
        try:
            delta[key] = int(after.get(key, "0")) - int(before.get(key, "0"))
        except (TypeError, ValueError):
            delta[key] = {"before": before.get(key), "after": after.get(key)}
    return delta
