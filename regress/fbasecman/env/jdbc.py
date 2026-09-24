from pathlib import Path

from framework.configuration import RegressionConfig


def prepare_jdbc(env: RegressionConfig) -> None:
    jdbc_dir = (env.root_dir / env.config["local"]["jdbc_lib_dir"]).resolve()
    jdbc_dir.mkdir(parents=True, exist_ok=True)
