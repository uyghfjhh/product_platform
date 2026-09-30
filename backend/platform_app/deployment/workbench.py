"""One desired specification -> compiled files -> checked immutable plan."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

import yaml

from ..config import ROOT
from ..filestore import ConflictError
from ..providers import provider_for
from .models import DeploymentSpec
from .probes import probe

ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
PROTECTED = {"port", "data_directory", "shared_preload_libraries", "wal_level", "hot_standby", "listen_addresses", "fdd.running_databases"}


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else json.dumps(value, sort_keys=True).encode()).hexdigest()


def safe_id(value):
    if not ID.fullmatch(value):
        raise ValueError("方案 ID 无效")
    return value


def templates(settings):
    from ..product_catalog import discover_products
    output = []
    for product in discover_products(settings.products_root).values():
        provider = provider_for(settings, product.id)
        method = getattr(provider, "deployment_templates", None)
        if method:
            output.extend({**item, "product_id": product.id, "product_title": product.title} for item in method(settings))
    return output


def compile_spec(settings, spec, environment_id):
    if not any(row["product_id"] == spec.product_id and row["id"] == spec.template_id for row in templates(settings)):
        raise ValueError("产品部署模板不存在")
    if any(key in PROTECTED or not re.fullmatch(r"[a-zA-Z_]\w*(?:\.\w+)*", key) for key in spec.parameters):
        raise ValueError("高级参数包含由拓扑管理的结构参数或无效名称")
    if spec.mode == "import":
        try:
            config = yaml.safe_load(spec.source_yaml)
        except yaml.YAMLError as exc:
            raise ValueError("导入 YAML 格式错误") from exc
        if not isinstance(config, dict) or not spec.target:
            raise ValueError("导入需要有效 pgcluster YAML 和目标")
        return config, spec.target, {}
    if not all((spec.home, spec.data_root, spec.license_file)):
        raise ValueError("请填写数据库安装目录、数据根目录及 License 文件")
    if Path(spec.data_root).is_relative_to(ROOT) or Path(spec.home).is_relative_to(ROOT / "data"):
        raise ValueError("数据库安装和数据目录不能位于平台控制面目录")
    return provider_for(settings, spec.product_id).compile_deployment(settings, spec, environment_id)


VALIDATE_SCRIPT = '''import json,sys
from pgclusterlib.config import load
from pgclusterlib.runtime import Runtime
config=load(sys.argv[1]);target=sys.argv[2];config.validate(target)
names=Runtime(config).target_instances(target)
print(json.dumps({"nodes":[{"name":name,"host":config.instance(name)["host_config"]["address"],"port":config.instance(name)["port"],"data_dir":config.instance(name)["data_dir"],"installation":config.instance(name)["installation"]} for name in names],"installations":config.installations}))
'''


def validate_config(settings, path, target):
    try:
        result = subprocess.run([sys.executable, "-c", VALIDATE_SCRIPT, str(path), target], cwd=settings.pgcluster_root,
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError("部署结构校验失败：" + result.stderr[-1500:])
        facts = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValueError("部署引擎不可用：" + str(exc)) from exc
    nodes = facts["nodes"]
    if not nodes or len(nodes) > 64:
        raise ValueError("方案需要 1–64 个节点")
    ports, paths = set(), set()
    for node in nodes:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]*", node["host"]):
            raise ValueError("目标主机地址无效")
        key = (node["host"], node["port"])
        path = Path(node["data_dir"])
        if not path.is_absolute() or ".." in path.parts or path == Path("/") or path.is_relative_to(ROOT):
            raise ValueError("实例数据目录无效或位于平台项目目录")
        if key in ports or (node["host"], str(path)) in paths:
            raise ValueError("同一主机的端口或数据目录重复")
        ports.add(key)
        for host, previous in paths:
            if host == node["host"] and (path.is_relative_to(previous) or Path(previous).is_relative_to(path)):
                raise ValueError("实例数据目录不能互相包含")
        paths.add((node["host"], str(path)))
    return facts


def inspect_plan(plan):
    facts = plan["facts"]
    checks = []
    groups = {}
    for node in facts["nodes"]:
        groups.setdefault((node["host"], node["installation"]), []).append(node)
    for (host, installation), nodes in groups.items():
        try:
            result = probe(host, {"operation": "inspect", "mode": plan["spec"]["mode"],
                                 "installation": facts["installations"][installation], "nodes": nodes})
            checks.extend({**check, "host": host} for check in result["checks"])
        except ValueError as exc:
            checks.append({"title": "目标主机探测", "host": host, "ok": False, "detail": str(exc)})
    canonical = []
    for check in checks:
        detail = check.get("detail")
        if isinstance(detail, dict) and "canonical_path" in detail:
            key = (check["host"], detail["canonical_path"])
            for host, path in canonical:
                if host == key[0] and (Path(key[1]).is_relative_to(path) or Path(path).is_relative_to(key[1])):
                    checks.append({"title": "实际数据目录冲突", "host": host, "ok": False, "detail": key[1]})
            canonical.append(key)
    return checks


class Workbench:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.drafts = settings.data_dir / "deployment-drafts"
        self.plans = settings.data_dir / "deployment-plans"

    def draft_path(self, key):
        return self.drafts / (safe_id(key) + ".json")

    def draft(self, key):
        path = self.draft_path(key)
        if not path.is_file():
            raise KeyError("部署草稿不存在")
        return json.loads(path.read_text())

    def save(self, key, spec, expected_revision):
        key = key or "env-" + uuid.uuid4().hex[:16]
        path = self.draft_path(key)
        with self.store._locked():
            old = self.draft(key) if path.is_file() else None
            if (old["revision"] if old else 0) != expected_revision:
                raise ConflictError("草稿已变化，请重新加载")
            if self.store.get_environment(key) and old is None:
                # Existing environment adoption starts only through import_current.
                raise ConflictError("已有环境需要先导入当前配置")
            row = {"schema_version": "1", "id": key, "revision": expected_revision + 1, "spec": spec.model_dump()}
            self.store._atomic_write(path, json.dumps(row, ensure_ascii=False))
        return row

    def import_current(self, key):
        environment = self.store.get_environment(key)
        if not environment or not environment.get("deployment_config"):
            raise KeyError("当前环境没有可导入的部署配置")
        existing = self.draft_path(key)
        if existing.is_file():
            return self.draft(key)
        candidates = [row for row in templates(self.settings) if row["product_id"] == environment["product_id"]]
        if not candidates:
            raise ValueError("当前产品没有部署模板")
        config = Path(environment["deployment_config"]).read_text()
        facts = validate_config(self.settings, Path(environment["deployment_config"]), environment["deployment_target"])
        spec = DeploymentSpec(title=environment["title"], product_id=environment["product_id"],
            template_id=candidates[0]["id"], mode="import", host=environment["host"],
            source_yaml=config, target=environment["deployment_target"], base_port=environment["port"])
        row = {"schema_version": "1", "id": key, "revision": 1, "spec": spec.model_dump()}
        with self.store._locked():
            if existing.is_file():
                return self.draft(key)
            self.store._atomic_write(existing, json.dumps(row, ensure_ascii=False))
        return row

    def generate(self, key):
        draft = self.draft(key)
        spec = DeploymentSpec(**draft["spec"])
        config, target, auxiliary = compile_spec(self.settings, spec, key)
        plan_id = uuid.uuid4().hex
        directory = self.plans / plan_id
        directory.mkdir(parents=True)
        content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        path = directory / "pgcluster.yaml"
        path.write_text(content)
        facts = validate_config(self.settings, path, target)
        if any(node["host"] != spec.host for node in facts["nodes"]):
            raise ValueError("首批工作台只支持同一目标主机，导入配置的主机必须与表单一致")
        for name, text in auxiliary.items():
            if Path(name).name != name:
                raise ValueError("产品辅助文件名称无效")
            (directory / name).write_text(text)
        # Imports preserve compatible auxiliary files rather than discarding test mappings.
        previous = self.store.get_environment(key)
        if spec.mode == "import" and previous and previous.get("deployment_config"):
            previous_dir = Path(previous["deployment_config"]).parent
            for name in ("regress.override.yaml", "regress.yaml"):
                old = previous_dir / name
                if old.is_file():
                    (directory / name).write_text(old.read_text())
        plan = {"id": plan_id, "environment_id": key, "draft_revision": draft["revision"], "spec": spec.model_dump(),
                "config_path": str(path.resolve()), "config_sha256": digest(content), "target": target,
                "baseline": previous, "facts": facts,
                "files": {file.name: digest(file.read_text()) for file in directory.iterdir() if file.is_file()}}
        plan["checks"] = inspect_plan(plan)
        plan["ready"] = bool(plan["checks"]) and all(check["ok"] for check in plan["checks"])
        plan["action"] = "deployment.create" if spec.mode == "new" else "deployment.health"
        plan["operations"] = [{"node": node["name"], "host": node["host"], "port": node["port"], "data_dir": node["data_dir"],
             "operation": "初始化、启动并建立复制关系" if spec.mode == "new" else "接管登记并检查健康（不初始化）"} for node in facts["nodes"]]
        plan["limitations"] = ["首批使用已有数据库安装和当前用户的本地／SSH 权限", "这是新建或接管计划，不是现有集群修改的全量差异计划", "部署失败不会自动删除数据；重试需要重新检查"]
        self.store._atomic_write(directory / "plan.json", json.dumps(plan, ensure_ascii=False))
        return plan

    def plan(self, key):
        path = self.plans / safe_id(key) / "plan.json"
        if not path.is_file():
            raise KeyError("部署计划不存在")
        plan = json.loads(path.read_text())
        directory = path.parent.resolve()
        if Path(plan["config_path"]).resolve() != directory / "pgcluster.yaml":
            raise ConflictError("计划配置路径已变化")
        for name, sha in plan["files"].items():
            if Path(name).name != name:
                raise ConflictError("计划文件名称无效")
            file = directory / name
            if not file.is_file() or digest(file.read_text()) != sha:
                raise ConflictError("生成文件已变化，请重新生成计划")
        return plan

    def environment(self, plan):
        primary = plan["facts"]["nodes"][0]
        spec = plan["spec"]
        return {"id": plan["environment_id"], "product_id": spec["product_id"], "title": spec["title"], "host": spec["host"],
                "port": primary["port"], "database_name": "postgres", "database_user": "postgres",
                "deployment_config": plan["config_path"], "deployment_target": plan["target"]}

    def verify(self, key, *, inspect=True):
        plan = self.plan(key)
        if self.draft(plan["environment_id"])["revision"] != plan["draft_revision"]:
            raise ConflictError("草稿已修改，请重新生成计划")
        current = self.store.get_environment(plan["environment_id"])
        expected = self.environment(plan)
        if current != plan["baseline"] and not (current and all(current.get(k) == v for k, v in expected.items())):
            raise ConflictError("环境配置已变化，请重新生成计划")
        if not plan["ready"]:
            raise ValueError("方案有未通过的检查，不能应用")
        if inspect:
            checks = inspect_plan(plan)
            if not all(check["ok"] for check in checks):
                raise ValueError("目标主机状态已变化，请重新检查方案")
        return plan

    def associate(self, key):
        plan = self.verify(key)
        expected = self.environment(plan)
        from ..actions import environment_lock
        with environment_lock(self.settings.platform_dir, expected["id"]):
            if self.store._has_active_task(expected["id"]):
                raise ConflictError("环境有活动任务，请等待任务完成")
            # Re-check under the environment lock before publishing the pointer.
            self.verify(key, inspect=False)
            profile = self.settings.data_dir / "profiles" / expected["id"]
            for name in plan["files"]:
                text = (Path(plan["config_path"]).parent / name).read_text()
                self.store._atomic_write(profile / name, text)
            if self.store.get_environment(expected["id"]):
                self.store.update_environment(expected["id"], expected)
            else:
                self.store.put_environment(expected)
        return expected


def worker_environment(settings, store, environment, snapshot):
    service = Workbench(settings, store)
    plan = service.plan(snapshot["plan_id"])
    if plan["environment_id"] != environment["id"] or plan["config_sha256"] != snapshot["sha256"]:
        raise ValueError("部署任务快照不匹配")
    checks = inspect_plan(plan)
    if not all(check["ok"] for check in checks):
        raise ValueError("执行前检查失败：节点端口、数据目录或安装发生变化")
    return service.environment(plan)
