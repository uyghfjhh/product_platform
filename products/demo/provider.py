"""Reference adapter connecting a product case to platform operations."""

import json
import sys
from pathlib import Path

from platform_app.providers import CommandSpec


PRODUCT_DIR = Path(__file__).parent
TARGET = "smoke.context"


class DemoProvider:
    def discover(self, settings):
        return [{
            "suite": "smoke", "suite_title": "接入验证",
            "target": TARGET, "name": "context", "title": "环境上下文",
            "summary": "检查平台向用例传递的环境 ID", "enabled": True, "tags": [],
        }]

    def validate_target(self, settings, target):
        return target in {TARGET, "smoke", "all"}

    def command(self, settings, environment, action, target, parameters):
        if action != "tests.demo" or not self.validate_target(settings, target):
            raise ValueError("unsupported demo action or target")
        output = settings.output_dir / "regression" / environment["id"] / target
        command = [
            sys.executable, "-m", "platform_regress.cli",
            "--product-dir", str(PRODUCT_DIR), "--output-dir", str(output),
            "--context-json", json.dumps({"id": environment["id"]}),
        ]
        command.extend(["--suite", "smoke"] if target in {"smoke", "all"} else [target])
        return CommandSpec(command, PRODUCT_DIR)

    def publish_result(self, store, settings, environment, task, terminal, reason):
        if task["action"] != "tests.demo":
            return terminal, reason
        from platform_app.result_publication import publish_regression_results
        parameters = json.loads(task["parameters"])
        return publish_regression_results(store, settings, environment, task, terminal, reason,
                                          case_targets={TARGET},
                                          profile=parameters.get("profile", "default"))

    def observe_database(self, environment):
        return []

    def observe_runtime(self, settings, environment):
        return []


PROVIDER = DemoProvider()
