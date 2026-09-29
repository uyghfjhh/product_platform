"""pgjdbc driver helpers shared by product test packages.

Products keep their own ``.java`` driver sources, phase markers and business
assertions.  The platform owns jar resolution, JDBC URL construction and
javac/java argv building; phased stdin control lives in
``platform_regress.execution.phased_process`` and JDBC evidence parsers in
``platform_regress.evidence.jdbc``.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


class JdbcError(RuntimeError):
    """A JDBC driver asset, jar or invocation contract failed."""


def _version_key(path):
    return tuple(int(part) for part in re.findall(r"\d+", path.stem))


def resolve_jar(lib_dir, version=None, pattern="postgresql-%s.jar"):
    """Return the pgjdbc jar under ``lib_dir`` or raise JdbcError.

    ``version=None`` selects the newest matching jar by numeric parts.
    """
    lib_dir = Path(lib_dir)
    if version is None:
        stem = pattern.split("%", 1)[0]
        candidates = list(lib_dir.glob(stem + "*.jar"))
        if not candidates:
            raise JdbcError("missing jdbc jar: %s" % (lib_dir / (stem + "*.jar")))
        return max(candidates, key=_version_key)
    jar = lib_dir / (pattern % version)
    if not jar.exists():
        raise JdbcError("missing jdbc jar: %s" % jar)
    return jar


def build_url(host, port, database="postgres", options=None):
    """Build a pgjdbc URL, skipping options whose value is None."""
    params = []
    for key, value in (options or {}).items():
        if value is not None:
            params.append("%s=%s" % (key, value))
    return "jdbc:postgresql://%s:%s/%s?%s" % (host, port, database, "&".join(params))


def classpath(*entries):
    """Join jar/directory entries into a Java -cp argument."""
    return os.pathsep.join(str(entry) for entry in entries)


def javac_argv(jar, source, dest_dir=None, extra_classpath=()):
    """Build a javac argv list without executing it."""
    cp = classpath(jar, *extra_classpath)
    argv = ["javac", "-cp", cp]
    if dest_dir is not None:
        argv += ["-d", str(dest_dir)]
    argv.append(str(source))
    return argv


def java_argv(cp, class_name, *arguments):
    """Build a java argv list; ``cp`` is a pre-built classpath string."""
    return ["java", "-cp", cp, class_name, *list(arguments)]


def source_file(assets_dir, filename):
    """Return a checked-in driver source under ``assets_dir`` or raise."""
    path = Path(assets_dir) / filename
    if not path.exists():
        raise JdbcError("missing jdbc driver source: %s" % path)
    return path


def stage_source(source, target_dir):
    """Copy a driver source into the case driver build directory."""
    source = Path(source)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copyfile(str(source), str(target))
    return target
