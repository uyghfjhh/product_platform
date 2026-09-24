from pathlib import Path

from framework.configuration import RegressionConfig


def check_license_dir(env: RegressionConfig) -> Path:
    license_dir = Path(env.config["fbasecman"]["license_dir"])
    if not license_dir.exists():
        raise FileNotFoundError(f"license_dir does not exist: {license_dir}")
    return license_dir
