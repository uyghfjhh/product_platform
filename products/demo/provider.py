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
        output = settings.environment_dir / "regression" / environment["id"] / target
        command = [
            sys.executable, "-m", "platform_regress.cli",
            "--product-dir", str(PRODUCT_DIR), "--output-dir", str(output),
            "--context-json", json.dumps({"id": environment["id"]}),
        ]
        command.extend(["--suite", "smoke"] if target in {"smoke", "all"} else [target])
        return CommandSpec(command, PRODUCT_DIR)

    def publish_result(self, store, settings, environment, task, terminal, reason):
        output = settings.environment_dir / "regression" / environment["id"] / task["target"]
        result_file = output / ("suite-result.json" if task["target"] in {"smoke", "all"} else "result.json")
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            if task["target"] in {"smoke", "all"}:
                verdict = "PASS" if result["counts"].get("PASS") == 1 else "ERROR"
            elif result.get("operation_id") == task["id"] and result.get("target") == task["target"]:
                verdict = result["verdict"]
            else:
                verdict = "ERROR"
        except (OSError, ValueError, KeyError, TypeError):
            verdict = "ERROR"
        if verdict == "ERROR":
            terminal, reason = "FAILED", "本次没有可核对的回归结果"
        store.put_result("demo", environment["id"], task["target"], "default",
                         verdict, reason, str(output))
        return terminal, reason

    def observe_database(self, environment):
        return []

    def observe_runtime(self, settings, environment):
        return []


PROVIDER = DemoProvider()
