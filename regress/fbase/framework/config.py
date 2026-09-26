import os
import re
from pathlib import Path

import yaml

from framework.errors import ConfigError


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def expand_environment(value, environ=None):
    environ = os.environ if environ is None else environ
    if isinstance(value, dict):
        return {key: expand_environment(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_environment(item, environ) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match):
        name = match.group(1)
        default = match.group(2)
        actual = environ.get(name, "")
        if not actual and default is not None:
            return default
        return actual

    return _ENV_PATTERN.sub(replace, value)


class RegressionConfig:
    def __init__(self, root, data):
        self.root = Path(root).resolve()
        self.data = data
        self.postgres = data.get("postgres") or {}
        self.clusters = data.get("clusters") or {}
        self._validate()

    @property
    def postgres_home(self):
        return Path(self.postgres["home"])

    @property
    def license_file(self):
        return Path(self.postgres["license_file"])

    def cluster(self, name):
        try:
            return self.clusters[name]
        except KeyError:
            choices = ", ".join(sorted(self.clusters))
            raise ConfigError("未知 cluster: %s（可选: %s）" % (name, choices))

    def template_path(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def _validate(self):
        home = self.postgres.get("home", "")
        license_file = self.postgres.get("license_file", "")
        if not home:
            raise ConfigError("postgres.home 为空，请设置 PGHOME 或直接填写安装目录")
        if not Path(home).is_absolute():
            raise ConfigError("postgres.home 必须是绝对路径: %s" % home)
        if not license_file or not Path(license_file).is_absolute():
            raise ConfigError("postgres.license_file 必须是非空绝对路径")
        if not self.clusters:
            raise ConfigError("clusters 不能为空")

        ports = set()
        data_dirs = set()
        for cluster_name, cluster in self.clusters.items():
            plugins = cluster.get("plugins") or {}
            for plugin in plugins:
                self._require_identifier(plugin, "clusters.%s.plugins" % cluster_name)
            nodes = cluster.get("nodes") or {}
            if not nodes:
                raise ConfigError("clusters.%s.nodes 不能为空" % cluster_name)
            for node_name, node in nodes.items():
                for field in ("host", "port", "data_dir"):
                    if node.get(field, "") == "":
                        raise ConfigError("节点 %s.%s 缺少 %s" % (cluster_name, node_name, field))
                try:
                    port = int(node["port"])
                except (TypeError, ValueError):
                    raise ConfigError("节点 %s.%s 的 port 不是整数" % (cluster_name, node_name))
                if port < 1 or port > 65535:
                    raise ConfigError("节点 %s.%s 的 port 超出范围" % (cluster_name, node_name))
                endpoint = (str(node["host"]), port)
                if endpoint in ports:
                    raise ConfigError("节点地址和端口重复: %s:%s" % endpoint)
                ports.add(endpoint)
                data_dir = Path(str(node["data_dir"]))
                if not data_dir.is_absolute():
                    raise ConfigError("节点 %s.%s 的 data_dir 必须是绝对路径" % (cluster_name, node_name))
                if str(data_dir) in data_dirs:
                    raise ConfigError("data_dir 重复: %s" % data_dir)
                data_dirs.add(str(data_dir))
            self._validate_groups(cluster_name, cluster, nodes)

    def _validate_groups(self, cluster_name, cluster, nodes):
        groups = cluster.get("groups") or {}
        unknown = set(groups) - {"streaming", "logical", "mmr"}
        if unknown:
            raise ConfigError("cluster %s 包含未知 group: %s" %
                              (cluster_name, ", ".join(sorted(unknown))))

        streaming = groups.get("streaming")
        if streaming:
            self._require_node(cluster_name, nodes, streaming.get("primary"), "streaming.primary")
            for node in streaming.get("standbys") or []:
                self._require_node(cluster_name, nodes, node, "streaming.standbys")

        logical = groups.get("logical")
        if logical:
            self._require_identifier(logical.get("publication_name"), "publication_name")
            self._require_node(cluster_name, nodes, logical.get("publisher"), "logical.publisher")
            for node, options in (logical.get("subscribers") or {}).items():
                self._require_node(cluster_name, nodes, node, "logical.subscribers")
                self._require_identifier(options.get("subscription_name"), "subscription_name")
                self._require_identifier(options.get("slot_name"), "slot_name")

        mmr = groups.get("mmr")
        if mmr:
            if "fdd_mmr" not in (cluster.get("plugins") or {}):
                raise ConfigError("cluster %s 的 mmr group 要求启用 fdd_mmr" % cluster_name)
            members = mmr.get("members") or {}
            if len(members) < 2:
                raise ConfigError("cluster %s 的 mmr group 至少需要两个 member" % cluster_name)
            self._require_identifier(mmr.get("group_name"), "mmr.group_name")
            for member, relation in members.items():
                self._require_identifier(member, "mmr member")
                self._require_node(cluster_name, nodes, relation.get("primary"), "mmr.primary")
                for node in relation.get("standbys") or []:
                    self._require_node(cluster_name, nodes, node, "mmr.standbys")

    @staticmethod
    def _require_node(cluster_name, nodes, node, field):
        if node not in nodes:
            raise ConfigError("cluster %s 的 %s 引用了未知节点: %s" %
                              (cluster_name, field, node))

    @staticmethod
    def _require_identifier(value, field):
        if not value or len(value.encode("utf-8")) > 63 or not _IDENTIFIER.match(value):
            raise ConfigError("%s 不是合法的 PostgreSQL 标识符: %s" % (field, value))


def load_config(root):
    root = Path(root).resolve()
    path = root / "regress.yaml"
    if not path.is_file():
        raise ConfigError("配置文件不存在: %s" % path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
    except yaml.YAMLError as exc:
        raise ConfigError("regress.yaml 解析失败: %s" % exc)
    return RegressionConfig(root, expand_environment(raw))
