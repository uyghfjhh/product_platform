"""Shared workload provider and metric facts."""

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..providers import CommandSpec

PRESETS = {
    "connectivity": "SELECT 1;",
    "catalog": "SELECT count(*) FROM pg_catalog.pg_class;",
}


class WorkloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    clients: int = Field(default=4, ge=1, le=128)
    jobs: int | None = Field(default=None, ge=1, le=128)
    duration_seconds: int = Field(default=30, ge=1, le=86400)
    preset: Literal["connectivity", "catalog"] = "connectivity"
    script: str | None = Field(default=None, min_length=1, max_length=64000)
    target_tps: float = Field(default=0, ge=0, le=1000000)
    connect_per_transaction: bool = False
    statement_timeout_seconds: int = Field(default=10, ge=1, le=60)
    minimum_tps: float = Field(default=0, ge=0)
    max_average_latency_ms: float = Field(default=0, ge=0)
    jdbc_jar: str = ""
    environment_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator('script')
    @classmethod
    def validate_sql(cls, value):
        if value is not None:
            from .sql import validate_script
            validate_script(value)
        return value

    @model_validator(mode='after')
    def validate_jobs(self):
        if self.jobs is not None and self.jobs > self.clients:
            raise ValueError('线程数不能超过客户端数')
        return self


def resolve_driver(environment, driver, options):
    import yaml
    from .sql import validate_script

    if not all(environment.get(key) for key in ('host', 'port', 'database_name', 'database_user')):
        raise ValueError('登记环境缺少实际连接目标、数据库或用户')
    if driver not in {'pgbench', 'jdbc'}:
        raise ValueError('未知负载引擎')
    if driver == 'jdbc' and (options.target_tps or options.connect_per_transaction or options.jobs is not None):
        raise ValueError('当前 JDBC 引擎不支持目标速率、短连接或线程数覆盖')
    if driver == 'jdbc' and validate_script(options.script or PRESETS[options.preset]) != 1:
        raise ValueError('当前 JDBC 负载只支持单条查询')
    home = ""
    if environment.get("deployment_config"):
        config = yaml.safe_load(Path(environment["deployment_config"]).read_text())
        home = next(
            iter((config.get("postgresql_installations") or {}).values()), {}
        ).get("home", "")
    candidate = str(Path(home) / "bin/pgbench") if home else ""
    pgbench = (
        candidate
        if candidate and Path(candidate).is_file()
        else shutil.which("pgbench")
    )
    if driver == "pgbench" and (not pgbench or not Path(pgbench).is_file()):
        raise ValueError("该环境没有可用 pgbench")
    if driver == "jdbc" and (
        not Path(options.jdbc_jar).is_absolute()
        or not Path(options.jdbc_jar).is_file()
        or not shutil.which("java")
        or not shutil.which("javac")
    ):
        raise ValueError("JDBC 负载需要可用 JDK 和驱动 jar 的绝对路径")
    return pgbench


def command(settings, environment, action, parameters):
    from .sql import environment_fingerprint

    options = WorkloadInput.model_validate(
        {key: value for key, value in parameters.items() if not key.startswith('_')}
    )
    driver = action.split('.')[1]
    if options.environment_fingerprint and options.environment_fingerprint != environment_fingerprint(environment):
        raise ValueError('审阅后环境发生变化，请重新生成负载计划')
    pgbench = resolve_driver(environment, driver, options)
    options.script = options.script or PRESETS[options.preset]
    identity = parameters.get("_workload_task_id")
    if not identity or not re.fullmatch(r"[a-f0-9-]{36}", identity):
        raise ValueError("工作负载缺少执行身份")
    output = (
        settings.artifact_dir(environment["product_id"], environment["id"])
        / "runs"
        / identity
        / "workload"
    )
    context = {
        "environment": {
            key: environment[key]
            for key in ("host", "port", "database_name", "database_user")
        },
        "options": options.model_dump(),
        "driver": driver,
        "pgbench": pgbench,
        "output": str(output),
        "execution_id": identity,
    }
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "request.json"
    config_path.write_text(json.dumps(context))
    return CommandSpec(
        [sys.executable, str(Path(__file__).with_name("runner.py")), str(config_path)],
        settings.products_root.parent,
    )


def publish(store, settings, environment, task_id, success):
    path = (
        settings.artifact_dir(environment["product_id"], environment["id"])
        / "runs"
        / task_id
        / "workload"
    )
    summary = (
        json.loads((path / "result.json").read_text())
        if (path / "result.json").is_file()
        else {}
    )
    task=store.tasks.get_task(task_id)
    cancelled=bool(task and task['cancel_requested'])
    store.results.put_result(
        environment["product_id"],
        environment["id"],
        (task['target'] if task['target'] not in {'all', environment['id']} else task['action'])
        if task else "workload." + summary.get("driver", "unknown"),
        "workload",
        "CANCELLED" if cancelled else "PASS" if success else "FAIL",
        "工作负载已取消" if cancelled else summary.get("reason"),
        str(path),
    )
