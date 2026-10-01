"""Canonical artifact fixtures shared by API, CLI and product tests."""
from pathlib import Path


def latest_run(root):
    return max((Path(root) / 'runs').iterdir(), key=lambda p: (p / 'run.json').stat().st_mtime)


def latest_case(settings, product, environment, target):
    return latest_run(settings.artifact_dir(product, environment)) / 'cases' / target
