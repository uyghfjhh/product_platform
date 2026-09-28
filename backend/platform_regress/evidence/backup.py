"""Backup-directory checkpoints: snapshot, set diff, and content compare.

Generic primitives for "the product wrote a new ``.bak`` file" assertions:
capture the directory state before a mutation, then diff the file set and
compare contents afterwards.  Report wording stays with the product.
"""

from pathlib import Path


class BackupCheckpoint(object):
    """Snapshot of backup file names plus the config bytes at capture time."""

    def __init__(self, files, config_content, backup_dir=None):
        self.files = frozenset(files)
        self.config_content = config_content
        self.backup_dir = backup_dir


def backup_dir_path(config_path, backup_dir=None):
    if backup_dir is not None:
        return Path(backup_dir)
    return Path(config_path).parent / "conf-backup"


def snapshot_backup(config_path, backup_dir=None):
    """Capture the current ``.bak`` file set and the config bytes."""
    backup_dir = backup_dir_path(config_path, backup_dir)
    files = tuple(path.name for path in backup_dir.iterdir()
                  if ".bak." in path.name) if backup_dir.is_dir() else ()
    return BackupCheckpoint(files, Path(config_path).read_bytes(), backup_dir)


def backup_files(backup_dir):
    if not backup_dir or not Path(backup_dir).is_dir():
        return set()
    return set(path.name for path in Path(backup_dir).iterdir()
               if ".bak." in path.name)


def created_backups(checkpoint, backup_dir=None):
    """Return ``(created_names, current_names)`` relative to the checkpoint."""
    current = backup_files(backup_dir or checkpoint.backup_dir)
    return sorted(current - set(checkpoint.files)), current


def backup_content_matches(checkpoint, backup_dir, name):
    return (Path(backup_dir) / name).read_bytes() == checkpoint.config_content
