"""Pure semantic diff and executable operation derivation between two pgcluster configs."""

from .change_execution import configurable, movable, removable
from .endpoint_metadata import endpoint_contexts
from .logical_changes import logical_editable
from .member_changes import member_delta, new_member_streams, retired_member_streams
from .primary_migration import primary_cluster

# 差异对比已识别的顶层段；其余任何顶层键变化都归入 sections 兜底拦截。
_TOPOLOGY_SECTIONS = (
    "streaming_clusters", "mmr_clusters", "logical_replications", "citus_clusters",
)
_KNOWN_SECTIONS = {
    "instances", "postgresql_config", "hosts", "postgresql_installations",
    *_TOPOLOGY_SECTIONS,
}


def _section_fields(before, after):
    """Named-object diff inside a config section: added/removed/changed(+fields)."""
    before = before or {}
    after = after or {}
    changed = []
    for name in sorted(set(before) & set(after)):
        old, new = before[name], after[name]
        if old == new:
            continue
        fields = {
            field: {"from": old.get(field), "to": new.get(field)}
            for field in sorted(set(old or {}) | set(new or {}))
            if (old or {}).get(field) != (new or {}).get(field)
        }
        changed.append({"name": name, "fields": fields} if fields else {"name": name})
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": changed,
    }


def diff_configs(current, desired):
    """Semantic diff between two pgcluster configs (order/formatting ignored)."""
    cur_instances = current.get("instances") or {}
    new_instances = desired.get("instances") or {}
    added = sorted(set(new_instances) - set(cur_instances))
    removed = sorted(set(cur_instances) - set(new_instances))
    changed = []
    for name in sorted(set(cur_instances) & set(new_instances)):
        old, new = cur_instances[name], new_instances[name]
        fields = {
            field: {"from": old.get(field), "to": new.get(field)}
            for field in ("host", "installation", "port", "data_dir")
            if old.get(field) != new.get(field)
        }
        if fields:
            changed.append({"name": name, "fields": fields})
    cur_config = current.get("postgresql_config") or {}
    new_config = desired.get("postgresql_config") or {}
    cur_params = cur_config.get("parameters") or {}
    new_params = new_config.get("parameters") or {}
    parameters = {
        key: {"from": cur_params.get(key), "to": new_params.get(key)}
        for key in sorted(set(cur_params) | set(new_params))
        if cur_params.get(key) != new_params.get(key)
    }
    # hba、replication_capacity 等 parameters 之外的 postgresql_config 键。
    config_extra = {
        key: {"from": cur_config.get(key), "to": new_config.get(key)}
        for key in sorted(set(cur_config) | set(new_config))
        if key != "parameters" and cur_config.get(key) != new_config.get(key)
    }
    hosts = _section_fields(current.get("hosts"), desired.get("hosts"))
    installations = _section_fields(
        current.get("postgresql_installations"),
        desired.get("postgresql_installations"),
    )
    topology = {}
    for section in _TOPOLOGY_SECTIONS:
        before = current.get(section) or {}
        after = desired.get(section) or {}
        if before != after:
            topology[section] = {
                "added": sorted(set(after) - set(before)),
                "removed": sorted(set(before) - set(after)),
                "changed": sorted(
                    name for name in set(before) & set(after)
                    if before[name] != after[name]
                ),
            }
    sections = sorted(
        key
        for key in set(current) | set(desired)
        if key not in _KNOWN_SECTIONS and current.get(key) != desired.get(key)
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "parameters": parameters,
        "config_extra": config_extra,
        "hosts": hosts,
        "installations": installations,
        "topology": topology,
        "sections": sections,
    }


def _added_node_executable(current, desired, name):
    """A new instance is executable only as a standby of an existing
    streaming cluster — idempotent `create <target>` materializes just it."""
    for cname, cluster in (desired.get("streaming_clusters") or {}).items():
        members = [cluster.get("primary")] + [
            row.get("instance") for row in cluster.get("standbys") or []
        ]
        if name not in members:
            continue
        if name == cluster.get("primary"):
            return False  # adding a primary to an existing cluster is invalid
        return cname in (current.get("streaming_clusters") or {})
    return False


def diff_operations(current, desired):
    """Turn a config diff into ordered operations with executability flags."""
    diff = diff_configs(current, desired)
    operations = []
    executable = True

    primary_moves = {
        row['name'] for row in diff['changed']
        if primary_cluster(current, desired, row['name'])
    }
    member_streams = new_member_streams(current, desired)
    member_nodes = {node for group in member_streams.values() for node in group['nodes']}
    retired_streams = retired_member_streams(current, desired)
    retired_nodes = {node for group in retired_streams.values() for node in group['nodes']}
    if primary_moves and (len(diff['changed']) != 1 or diff['added'] or diff['removed'] or diff['parameters'] or diff['topology']):
        executable = False
        operations.append({
            'kind': 'primary_migration_scope', 'executable': False,
            'operation': '主节点迁移须独立审阅，不能混合扩缩容、参数或其他节点变更',
        })
    for name in diff["added"]:
        if name in member_nodes:
            operations.append({
                'kind': 'create_member_instance', 'node': name, 'executable': True,
                'operation': '初始化新增复制成员的独立主备实例，仅允许新目录；随后加入复制组',
            })
        elif _added_node_executable(current, desired, name):
            operations.append(
                {
                    "kind": "add_standby",
                    "node": name,
                    "executable": True,
                    "operation": "扩容新增备库（发布配置后重放幂等 create，仅创建该节点）",
                }
            )
        else:
            executable = False
            operations.append(
                {
                    "kind": "add_node",
                    "node": name,
                    "executable": False,
                    "operation": "新增节点未接入既有流复制集群，暂不支持自动执行",
                }
            )
    for name in diff["removed"]:
        can_remove = removable(current, desired, name) or name in retired_nodes
        executable = executable and can_remove
        operations.append(
            {
                "kind": "remove_node",
                "node": name,
                "executable": can_remove,
                "operation": ("完成复制成员退出／分片排空后停止退役实例，移除活动配置，保留数据目录"
                              if name in retired_nodes else "停止退役备库并释放其声明的物理复制槽，保留数据目录" if can_remove else "主节点或拓扑成员不能作为物理备库缩容"),
            }
        )
    for row in diff["changed"]:
        is_primary = row['name'] in primary_moves
        can_move = movable(current, desired, row['name']) or is_primary
        executable = executable and can_move
        fields = "、".join(sorted(row["fields"]))
        primary_description, contexts = '', []
        if is_primary:
            old, new = current['instances'][row['name']], desired['instances'][row['name']]
            same_location = (current['hosts'][old['host']]['address'], old['data_dir']) == (
                desired['hosts'][new['host']]['address'], new['data_dir'])
            primary_description = (f'主库端口变更（{fields}）：正常停止后重启，保留原目录和复制槽，重连备库；写入有暂停'
                                   if same_location else f'主库迁移（{fields}）：先复制追平并检查逻辑槽/WAL，回放旧主库关闭栅栏后提升并重连备库；写入有暂停，原目录保留')
            contexts = endpoint_contexts(current, primary_cluster(current, desired, row['name'])) or []
            if contexts:
                primary_description += '；随后同步关联复制端点并验收'
        operations.append(
            {
                "kind": "change_node",
                "node": row["name"],
                "executable": can_move,
                "operation": (primary_description
                              if is_primary else f"备库变更（{fields}），新备库追平后停止原实例并保留原数据" if can_move else f"结构字段变更（{fields}）需先调整角色；跨目录备库迁移需要独立新槽"),
                "detail": row["fields"],
            }
        )
        if contexts:
            operations.append({
                'kind': 'replication_endpoint', 'node': row['name'], 'executable': True,
                'operation': '更新关联 MMR 成员 DSN／Citus 节点地址／逻辑订阅连接；保留节点标识、分片与原连接凭据',
                'detail': [{'kind': context['kind'], 'name': context['name'], 'role': context.get('role', 'member')} for context in contexts],
            })
    if diff["parameters"]:
        can_configure = configurable(diff["parameters"])
        executable = executable and can_configure
        operations.append(
            {
                "kind": "parameters",
                "executable": can_configure,
                "operation": "postgresql 参数差异 %d 项，按参数上下文热加载或分节点重启，并检查结果"
                % len(diff["parameters"]),
                "detail": diff["parameters"],
            }
        )
    if diff["config_extra"]:
        executable = False
        operations.append(
            {
                "kind": "config",
                "executable": False,
                "operation": "postgresql 配置段差异（%s）暂不支持自动应用"
                % "、".join(sorted(diff["config_extra"])),
                "detail": diff["config_extra"],
            }
        )
    for kind, key, title in (
        ("host", "hosts", "主机定义"),
        ("installation", "installations", "安装定义"),
    ):
        section = diff[key]
        if any((section["added"], section["removed"], section["changed"])):
            changed_nodes = set(diff['added']) | primary_moves | {row['name'] for row in diff['changed'] if movable(current,desired,row['name'])}
            field = 'host' if key == 'hosts' else 'installation'
            added_used = all(any(desired['instances'][name][field]==entry for name in changed_nodes) for entry in section['added'])
            removed_unused = all(all(node[field]!=entry for node in desired['instances'].values()) for entry in section['removed'])
            resource_change_allowed = not section['changed'] and added_used and removed_unused
            executable = executable and resource_change_allowed
            operations.append(
                {
                    "kind": kind,
                    "executable": resource_change_allowed,
                    "operation": (
                        "%s差异：新增 %s、移除 %s、修改 %s，地址/目录/凭据变化不支持自动执行"
                        % (
                            title,
                            section["added"] or "∅",
                            section["removed"] or "∅",
                            [row["name"] for row in section["changed"]] or "∅",
                        )
                    ),
                    "detail": section,
                }
            )
    for key in diff["sections"]:
        executable = False
        operations.append(
            {
                "kind": "section",
                "executable": False,
                "operation": "未识别的配置段 %s 发生变化，暂不支持自动执行" % key,
                "detail": {"from": current.get(key), "to": desired.get(key)},
            }
        )
    streaming_changes = diff["topology"].get("streaming_clusters")
    covered = set()
    if streaming_changes:
        for name in streaming_changes["changed"]:
            before = dict(current["streaming_clusters"][name])
            after = dict(desired["streaming_clusters"][name])
            b_standbys = before.pop("standbys") or []
            a_standbys = after.pop("standbys") or []
            b_members = {row['instance']: row for row in b_standbys}
            a_members = {row['instance']: row for row in a_standbys}
            if (before == after
                    and all(a_members[n] == b_members[n] or (movable(current,desired,n) and (set(a_members[n])-{'slot'}) == (set(b_members[n])-{'slot'}) and all(a_members[n][key]==b_members[n][key] for key in a_members[n] if key!='slot')) for n in a_members.keys() & b_members.keys())
                    and all(n in diff['added'] for n in a_members.keys() - b_members.keys())
                    and all(n in diff['removed'] and removable(current, desired, n)
                            for n in b_members.keys() - a_members.keys())):
                covered.add(name)
    for section, change in diff["topology"].items():
        change = dict(change)
        if section in {'mmr_clusters', 'citus_clusters', 'logical_replications'}:
            supported = []
            for name in change['changed']:
                delta = member_delta(current, desired, section, name) if section != 'logical_replications' else None
                if (delta is not None or (section == 'logical_replications' and logical_editable(current, desired, name))):
                    supported.append(name)
            if section == 'logical_replications':
                supported += [name for name in change['added'] + change['removed'] if logical_editable(current, desired, name)]
            if supported:
                independent = (set(diff['added']) <= member_nodes and set(diff['removed']) <= retired_nodes
                               and not (diff['changed'] or diff['parameters']))
                executable = executable and independent
                operations.append({
                    'kind': 'replication_members', 'executable': independent,
                    'operation': ({
                        'mmr_clusters': 'MMR 成员加入／正常退出，核对组标识、拒绝覆盖新增成员业务表，保留退役数据库',
                        'citus_clusters': 'Citus 注册新增 Worker；排空分片后移除退役 Worker，保留数据库',
                        'logical_replications': '逻辑复制链路增删／重建，核对订阅和复制槽；初始复制拒绝非空目标表，保留业务数据',
                    }[section] + ('；成员变更不能与其他节点迁移或参数变更混合' if not independent else '')),
                    'detail': {'section': section, 'names': supported},
                })
                for key in ('added', 'removed', 'changed'):
                    change[key] = [name for name in change[key] if name not in supported]
                if not any(change.values()):
                    continue
        if section == "streaming_clusters":
            change['added'] = [name for name in change['added'] if name not in member_streams]
            change['removed'] = [name for name in change['removed'] if name not in retired_streams]
            change["changed"] = [
                name for name in change["changed"] if name not in covered
            ]
            if not any(change.values()):
                continue
        executable = False
        operations.append(
            {
                "kind": "topology",
                "executable": False,
                "operation": (
                    f"拓扑段 {section} 变化：新增 {change['added'] or '∅'}、"
                    f"移除 {change['removed'] or '∅'}、修改 {change['changed'] or '∅'}，"
                    "暂不支持自动执行"
                ),
                "detail": change,
            }
        )
    return diff, operations, executable
