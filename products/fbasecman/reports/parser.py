"""Report parsing helpers for the fbasecman report dialog and topology view.

Moved verbatim from the vendored ``tools/web_reports.py`` so the platform
process can import the parser directly (no subprocess/sys.path juggling).
Only the ``parse_report`` closure is kept; the old Web list-page helpers
(``load_run_meta``/``case_status_meta``) had no consumers left.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Per-directory priority: most specific first (mirrors cmanconf layering).
_CONFIG_NAMES = (
    "regress.override.yaml",
    "regress.local.yaml",
    "regress.yaml",
    "stable.local.yaml",
    "stable.yaml",
)


def _deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _display_report_value(value):
    """Format structured facts for display without modifying stored evidence."""
    if value is None:
        return ""
    if isinstance(value, dict):
        labels = {"received": "响应类型", "rows": "查询结果", "sqlstates": "SQLSTATE",
                  "errors": "错误", "tags": "命令标签", "ready": "就绪状态", "parameters": "参数状态",
                  "transport_error": "连接异常", "planned": "计划数", "executed": "已执行",
                  "passed": "通过", "failed": "失败", "blocked": "阻塞", "unexecuted": "未执行"}
        return "\n".join(f"{labels.get(str(key), str(key))}：{_display_report_value(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        separator = "\n" if any(isinstance(item, str) and len(item) > 80 for item in value) else "；"
        return separator.join(_display_report_value(item) for item in value) if value else "（无）"
    return str(value)


def _present_guc_alignment_steps(steps):
    """Old/new GUC archives share the existing report UI with business labels."""
    from products.fbasecman.guc_alignment_native import (
        PROBE_SQL,
        SCENARIO_LABELS,
        operation_label,
    )
    from products.fbasecman.guc_report_contract import plan_scope, scenario_purpose
    archived_checks = {}
    for item in steps:
        for evidence in item.get('evidence') or []:
            if str(evidence).endswith('-wire.json'):
                archived_checks[Path(str(evidence)).name.removesuffix('-wire.json')] = item
    last_operation = "前序操作"
    parameters = {"work_mem", "statement_timeout", "TimeZone", "application_name"}
    for step in steps:
        command = str(step.get("command") or "")
        prefix = str(step.get("title", "")).split("：", 1)[0]
        prefix = prefix + "：" if "/" in prefix or "／" in prefix else ""
        actual = step.get("actual")
        expected = step.get("expected")
        if "子场景结论" in str(step.get("title", "")) and isinstance(actual, dict):
            scenario = actual.get('scenario') or next((name for name in SCENARIO_LABELS if name in step.get('title', '')), '')
            checks = []
            commands = []
            baseline = []
            measurements = []
            business_checks = []
            process_steps = []
            outcome_rows = []
            prior_values = {}
            transaction_open = False
            last_end = None
            transaction_operations = []
            last_measurement = None
            for recorded in actual.get('steps', []):
                item = archived_checks.get(recorded.get('key'), {})
                intent = recorded.get('intent', item.get('intent'))
                command = recorded.get('command') or item.get('command') or recorded.get('title')
                sql_text = str(command).split('：', 1)[-1].strip()
                if PROBE_SQL not in str(command) and intent in {'action', 'verify'} and isinstance(recorded.get('actual', item.get('actual')), dict) and 'received' in recorded.get('actual', item.get('actual')):
                    upper = sql_text.upper()
                    if upper.startswith('BEGIN'):
                        transaction_open = True
                        transaction_operations = [sql_text]
                        last_end = None
                    elif upper in {'COMMIT', 'ROLLBACK'}:
                        transaction_operations.append(sql_text)
                        transaction_open = False
                        last_end = upper
                    elif upper.startswith(('SET ', 'RESET ')):
                        if transaction_open:
                            transaction_operations.append(sql_text)
                if PROBE_SQL in str(command):
                    last_measurement = {'SQL': command, '响应附件': next(iter(item.get('evidence') or []), None)}
                title = recorded.get('title') or item.get('title', recorded.get('key', ''))
                observed = recorded.get('actual', item.get('actual'))
                if isinstance(observed, dict) and isinstance(recorded.get('expected', item.get('expected')), dict):
                    wanted = recorded.get('expected', item.get('expected'))
                    backend = observed.get('后端') or (observed if '端口' in observed else {})
                    parameter_names = [name for name in ('work_mem','statement_timeout','TimeZone','application_name') if name in wanted]
                    client_name = observed.get('客户端') or observed.get('client') or backend.get('客户端', '当前客户端')
                    for parameter in parameter_names:
                        if parameter in observed:
                            outcome_rows.append({'operation': recorded.get('title') or item.get('title', ''),
                                'client': observed.get('客户端') or observed.get('client') or backend.get('客户端', '当前客户端'),
                                'before': prior_values.get((client_name, parameter), '未归档'),
                                'transaction_stage': '事务内' if transaction_open else '提交后' if last_end == 'COMMIT' else '回滚后' if last_end == 'ROLLBACK' else '事务外／基线',
                                'transaction_commands': list(transaction_operations),
                                'parameter': parameter, 'expected': str(wanted[parameter]), 'actual': str(observed[parameter]),
                                'host': backend.get('主机', observed.get('host', '未归档')),
                                'port': backend.get('端口', observed.get('port', '未归档')),
                                'pid': backend.get('后端 PID', observed.get('pid', '未归档')),
                                'role': '备库' if backend.get('pg_is_in_recovery', observed.get('recovery')) == 'true' else '主节点' if backend.get('pg_is_in_recovery', observed.get('recovery')) == 'false' else '未归档',
                                'status': 'PASS' if recorded.get('passed') is True else 'FAIL' if recorded.get('passed') is False else item.get('status', '未保存'),
                                'sql': command, 'evidence': recorded.get('evidence') or next(iter(item.get('evidence') or []), None)})
                            prior_values[(client_name, parameter)] = str(observed[parameter])

                declared = recorded.get('expected', item.get('expected'))
                is_wire = isinstance(observed, dict) and 'received' in observed
                if is_wire:
                    output = []
                    if observed.get('rows'):
                        output.append('查询返回：\n' + '\n'.join(' | '.join('NULL' if v is None else str(v) for v in row) for row in observed['rows']))
                    if observed.get('tags'):
                        output.append('命令返回：' + '；'.join(observed['tags']))
                    if observed.get('sqlstates'):
                        output.append('SQLSTATE：' + '；'.join(str(v) for v in observed['sqlstates']))
                    if observed.get('parameters'):
                        output.append('客户端收到参数通知：' + _display_report_value(observed['parameters']))
                    actual_text = '\n'.join(output) or _display_report_value(observed)
                else:
                    actual_text = _display_report_value(observed)
                process_steps.append({'operation': title, 'command': command,
                    'expected': _display_report_value(declared), 'actual': actual_text,
                    'analysis': recorded.get('analysis') or item.get('analysis') or
                        ('本条仅记录命令响应；后续查询与路由检查才证明参数生效和节点正确' if is_wire else '比较本条声明期望与实测结果'),
                    'status': 'PASS' if recorded.get('passed') is True else 'FAIL' if recorded.get('passed') is False else item.get('status', '未保存'),
                    'driver': 'psql 客户端' if actual.get('protocol') == 'psql' else '原始 PostgreSQL 协议客户端' if is_wire else '实测比较',
                    'evidence': recorded.get('evidence') or next(iter(item.get('evidence') or []), None)})

                if any(label in str(title) for label in ('直连数据库读取本次 GUC 默认值', '记录物理默认值和代理会话初值')):
                    baseline.append(recorded.get('actual', item.get('actual')))
                if intent in {'action', 'verify'} and PROBE_SQL not in str(command) and command and (not commands or commands[-1] != command):
                    commands.append(str(command))
                if intent != 'verify':
                    continue
                title = recorded.get('title') or item.get('title', recorded.get('key'))
                expected_value = recorded.get('expected', item.get('expected'))
                actual_value = recorded.get('actual', item.get('actual'))
                if isinstance(actual_value, dict) and actual_value.get('采集 SQL'):
                    last_measurement = {'SQL': actual_value['采集 SQL'], '响应附件': actual_value.get('原始查询响应附件')}
                    measurements.append(last_measurement)
                    actual_value = {k: v for k, v in actual_value.items() if k not in {'采集 SQL', '原始查询响应附件'}}
                checks.append(f"{title}：期望 {_display_report_value(expected_value)}；实测 {_display_report_value(actual_value)}")
                measurement = last_measurement if (isinstance(expected_value, dict) and set(expected_value).issubset(parameters)) or (isinstance(actual_value, dict) and 'pg_is_in_recovery' in actual_value) else None
                business_checks.append({'operation': title, 'command': command,
                    'expected': _display_report_value(expected_value), 'actual': _display_report_value(actual_value),
                    'analysis': recorded.get('analysis') or item.get('analysis') or '以本条记录的预期与实测比较为依据',
                    'status': 'PASS' if recorded.get('passed') is True else 'FAIL' if recorded.get('passed') is False else item.get('status', '未保存'),
                    'measurement_sql': measurement['SQL'] if measurement else None,
                    'measurement_evidence': measurement['响应附件'] if measurement else None})
            scope = plan_scope(actual)
            scope['读写模式'] = str(step.get('title', '')).split('/')[1] if '/' in str(step.get('title', '')) else '未归档'
            scope['连接组'] = 'mmr_group' if actual.get('topology') == 'mmr' else 'rep_group'
            mode = str(step.get('title', '')).split('/')[1] if '/' in str(step.get('title', '')) else ''
            step['title'] = f"{scope['拓扑']}／{mode}／{SCENARIO_LABELS.get(scenario, scenario or '子场景')}：{scope['连接池'].split('（')[0]} · {scope['请求方式']} 验证汇总"
            step['expected'] = scenario_purpose({**actual, 'scenario': scenario})
            step['actual'] = {**plan_scope(actual), '已记录验证': checks or ('本配置未执行，不计为通过' if actual.get('executed') is False else '未保存可关联的验证明细，请查看前面的具体检查及原始证据'),
                              '子场景结论': actual.get('status', step.get('status'))}
            if measurements:
                step['actual']['参数与后端身份采集 SQL'] = PROBE_SQL
                step['actual']['原始查询响应附件'] = [m['响应附件'] for m in measurements if m['响应附件']]
            if baseline:
                step['actual']['默认值与初始状态'] = baseline
            step['business_checks'] = business_checks
            step['process_steps'] = process_steps
            step['outcome_rows'] = outcome_rows
            if scenario.startswith('tx_') or scenario == 'set_local_scope':
                step['transaction_report'] = True
                step['local_scope'] = scenario == 'set_local_scope'
                step['transaction_summary'] = ('SET LOCAL 只在当前事务内有效，不写入持久会话同步缓存。事务结束后应恢复会话值，再换后端确认 LOCAL 值没有残留。'
                    if scenario == 'set_local_scope' else '先核对事务内实际修改值，再核对提交／回滚后的会话值；最后检查换后端后的参数是否正确。事务内修改不代表事务内发生了连接切换。')
            if scenario.startswith('extended_'):
                for row in outcome_rows:
                    action = str(row.get('sql', ''))
                    if '未 E' in action:
                        branch = action.split('/Sync', 1)[0]
                        row['phase'] = {'P': '只解析 SQL（Parse），未执行', 'PB': '解析并绑定（Bind），未执行',
                                        'PDS': '解析并描述语句，未执行', 'PBDP': '解析、绑定并描述 Portal，未执行'}.get(branch, '准备语句／Portal，未执行')
                        row['meaning'] = '参数应保持原值，不能在准备阶段提前生效'
                    elif action.startswith('E/Sync') or 'E/Sync' in action:
                        row['phase'] = '真正执行已绑定的语句（Execute）'
                        row['meaning'] = '执行后才应用本条 SET／RESET 的目标值'
                    else:
                        row['phase'] = operation_label(action)
                        row['meaning'] = '以本条声明的目标值和实际查询比较'
                step['extended_boundary'] = True
                step['boundary_summary'] = '准备阶段不改变参数；执行阶段才应用参数。以下数值来自当次客户端实际查询，不代表仅凭 SQL 值已证明内部缓存。'

            step['baseline_context'] = _display_report_value(baseline) if baseline else ''
            step['report_scope'] = scope
            if actual.get('reason'):
                step['actual']['原因'] = actual['reason']
            step['analysis'] = (f"本配置记录了 {len(checks)} 项具体验证；子场景结论为 {actual.get('status', step.get('status'))}。"
                                if checks else '归档仅保留子场景结论，不能据此补造参数值或连接复用证据。')
            step["command"] = "\n".join(commands) if commands else "归档未保存可关联的操作明细"
        if isinstance(actual, dict) and "received" in actual:
            if PROBE_SQL not in command:
                last_operation = command
            if step.get("status") == "PASS" and isinstance(expected, dict) and not expected.get("sqlstates"):
                step["intent"] = "action"
                step["analysis"] = "请求已完成；参数是否正确由后续参数值与缓存检查判定，原始报文保留在证据中。"
            step["title"] = prefix + operation_label(command)
        elif isinstance(expected, dict) and expected and set(expected).issubset(parameters) and isinstance(actual, dict):
            step["title"] = prefix + operation_label(last_operation) + "后，核对会话参数"
            step["command"] = last_operation
            step["analysis"] = "；".join(f"{name}：期望 {value}，实际 {actual.get(name, '未返回')}"
                                           for name, value in expected.items())
            baseline = {"work_mem": "8MB", "statement_timeout": "7s", "TimeZone": "UTC"}
            if step.get("status") == "PASS" and "SET TimeZone='UTC'" in last_operation and all(
                    baseline.get(name) == value for name, value in expected.items()):
                step["intent"] = "prepare"
                step["title"] = prefix + "建立测试前的参数基线"
        title = step.get("title", "")
        for name, label in SCENARIO_LABELS.items():
            title = title.replace(name, label)
        step["title"] = title
    grouped = []
    for step in steps:
        expected, actual = step.get("expected"), step.get("actual")
        previous = grouped[-1] if grouped else None
        merge = (previous is not None and isinstance(expected, dict) and expected and
                 set(expected).issubset(parameters) and isinstance(actual, dict) and
                 isinstance(previous.get("expected"), dict) and previous["expected"] and
                 set(previous["expected"]).issubset(parameters) and
                 not set(expected).intersection(previous["expected"]) and
                 previous.get("command") == step.get("command") and
                 isinstance(previous.get("actual"), dict) and
                 previous["actual"].get("后端") == actual.get("后端"))
        if merge:
            if "grouped_checks" not in previous:
                previous["grouped_checks"] = [dict(previous)]
            previous["grouped_checks"].append(dict(step))
            previous["expected"] = {**previous["expected"], **expected}
            previous["actual"] = {**previous["actual"], **actual}
            previous["analysis"] += "；" + str(step.get("analysis", ""))
            if step.get("status") != "PASS":
                previous["status"] = step["status"]
            previous["assertion"] = {"type": "guc_parameters_equal", "passed": previous.get("status") == "PASS",
                                     "checks": [item.get("assertion") for item in previous["grouped_checks"]]}
        else:
            grouped.append(step)
    for step in grouped:
        expected, actual = step.get("expected"), step.get("actual")
        if (isinstance(expected, dict) and expected and set(expected).issubset(parameters)
                and isinstance(actual, dict)):
            differences = [f"{name}：期望 {value}，实际 {actual.get(name, '未返回')}"
                           for name, value in expected.items()
                           if str(actual.get(name)) != str(value)]
            # Keep the archived verdict; presentation must not infer a new PASS.
            if differences:
                step["analysis"] = "不匹配：" + "；".join(differences)
            elif step.get("status") == "PASS":
                step["analysis"] = "参数值与期望一致。"
                if "SET SESSION CHARACTERISTICS AS TRANSACTION READ" in str(step.get("command", "")):
                    step["analysis"] += " 此项只核对参数，不证明路由正确；路由依据是独立的目标地址、端口及角色确认，历史报告未保存此检查时不能补作已验证。"
    return grouped


def parse_psql_tables(raw_text: str) -> List[Tuple[List[str], List[Dict[str, str]]]]:
    """Parse one or more psql aligned tables from captured command output."""
    tables = []
    lines = raw_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and "+-" in lines[i + 1]:
            headers = [header.strip().lower() for header in line.split("|")]
            rows = []
            j = i + 2
            while j < len(lines):
                row_line = lines[j]
                if not row_line.strip() or row_line.strip().startswith("(") or "+-" in row_line:
                    break
                if "|" in row_line:
                    parts = [part.strip() for part in row_line.split("|")]
                    if len(parts) == len(headers):
                        rows.append(dict(zip(headers, parts)))
                j += 1
            if rows:
                tables.append((headers, rows))
            i = j
        else:
            i += 1
    return tables


def load_runtime_config(*root_dirs):
    """按目录优先级读取并深合并 yaml 配置（靠前的目录覆盖靠后的），
    用于发现数据库端口与代理端口。"""
    layers = []
    for root_dir in root_dirs:
        if not root_dir:
            continue
        for cfg_name in _CONFIG_NAMES:
            p = Path(root_dir) / cfg_name
            if p.is_file():
                try:
                    import yaml
                    data = yaml.safe_load(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        layers.append(data)
                        break
                except Exception:
                    pass
    merged = {}
    for layer in reversed(layers):
        merged = _deep_merge(merged, layer)
    return merged


def build_step_topology_snapshots(
    raw_text: str,
    base_clusters: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    proxy_write_port: int,
    proxy_read_port: int
) -> List[Dict[str, Any]]:
    """为报告中的每个测试步骤推演一份拓扑快照，驱动前端执行动画。

    原理：逐步骤扫描 psql 表格输出中的 topology_state / route_status，
    结合步骤标题里的关键词（故障/防抖/恢复）推断节点状态迁移，
    生成 初始 -> 故障 -> 降级/防抖 -> 恢复 的快照序列。
    """
    if not steps or not base_clusters:
        return []

    snapshots = []
    # current_cluster_states / current_node_states 跨步骤累积，表示"当前已知"状态
    current_cluster_states: Dict[str, str] = {c["name"]: "VALID" for c in base_clusters}
    current_node_states: Dict[str, Dict[str, Any]] = {}
    for c in base_clusters:
        for n in c["nodes"]:
            is_pri = n["name"] == c.get("primary") or n.get("role") == "PRIMARY"
            current_node_states[n["name"]] = {
                "role": "PRIMARY" if is_pri else "STANDBY",
                "state": "ACTIVE",
                "is_write": is_pri,
            }

    all_node_names = list(current_node_states.keys())

    for idx, st in enumerate(steps):
        title = st.get("title", f"步骤 {idx + 1}")
        action = st.get("action", "")
        expected = st.get("expected", "")
        actual = st.get("actual", "")
        cmd = st.get("command", "")
        st_text = st.get("state_table", "") + "\n" + actual + "\n" + action + "\n" + title + "\n" + cmd
        tables = parse_psql_tables(st_text)

        # 从本步骤的 psql 输出表格中提取集群状态与节点可用性
        step_cluster_table_states = {}
        avail_nodes = set()
        unavail_nodes = set()
        for headers, rows in tables:
            if "cluster_name" in headers and "topology_state" in headers:
                for r in rows:
                    c_n = r.get("cluster_name", "").strip()
                    s_t = r.get("topology_state", "").strip()
                    if c_n and s_t:
                        step_cluster_table_states[c_n] = s_t
            if "candidate_node" in headers and "route_status" in headers:
                for r in rows:
                    c_n = r.get("candidate_node", "").strip()
                    r_s = r.get("route_status", "").strip()
                    if c_n:
                        if r_s == "AVAILABLE":
                            avail_nodes.add(c_n)
                        else:
                            unavail_nodes.add(c_n)

        # 步骤语义分类：防抖 优先于 故障（防抖步骤里也常含 stop 动作）
        is_debounce = "防抖" in title or "防抖" in action
        is_fault = not is_debounce and (
            "故障" in title or "停机" in action or "剔除" in action or "stop" in cmd.lower()
            or "VALID_DEGRADED" in step_cluster_table_states.values()
            or "NO_READ_CANDIDATE" in st_text
        )
        is_recovery = "恢复" in title or "拉起" in action or "重新准入" in action or "refresh" in cmd.lower()

        for c_n, s_t in step_cluster_table_states.items():
            if c_n in current_cluster_states:
                current_cluster_states[c_n] = s_t

        event_desc = ""
        event_type = "normal"

        primary_nodes = {c.get("primary") for c in base_clusters if c.get("primary") and c.get("primary") != "-"}

        if idx == 0:
            event_type = "init"
            event_desc = "🚀 代理与探活初始化：集群拓扑就绪，全部节点处于健康探活态"
            for c_n in current_cluster_states:
                current_cluster_states[c_n] = "VALID"
            for n_n, n_st in current_node_states.items():
                is_pri = n_n in primary_nodes
                n_st["role"] = "PRIMARY" if is_pri else "STANDBY"
                n_st["state"] = "ACTIVE"
                n_st["is_write"] = is_pri
        elif is_debounce:
            event_type = "debounce"
            event_desc = "⚡ 探活防抖机制生效：节点短暂停机未达 3 次重试阈值，未触发误屏蔽，保持只读候选"
        elif is_fault:
            event_type = "fault"
            faulted_node_name = ""
            stopped_matches = re.findall(r"(?:stop|停止|宕机|剔除|故障)[^\n,]*?([a-zA-Z0-9_]+)", st_text)

            # 定位被停掉的节点：先精确匹配节点名，再按最长优先做子串匹配
            for sm in stopped_matches:
                for cand_n in all_node_names:
                    if cand_n == sm:
                        faulted_node_name = cand_n
                        break
                if faulted_node_name:
                    break

            if not faulted_node_name:
                sorted_by_len = sorted(all_node_names, key=len, reverse=True)
                for sm in stopped_matches:
                    for cand_n in sorted_by_len:
                        if cand_n in sm or sm in cand_n:
                            faulted_node_name = cand_n
                            break
                    if faulted_node_name:
                        break

            if not faulted_node_name and avail_nodes:
                for n_n in all_node_names:
                    if current_node_states[n_n]["role"] != "PRIMARY" and n_n not in avail_nodes:
                        faulted_node_name = n_n
                        break

            if not faulted_node_name and unavail_nodes:
                faulted_node_name = list(unavail_nodes)[0]

            if not faulted_node_name:
                for c in base_clusters:
                    if current_cluster_states.get(c["name"]) == "VALID_DEGRADED":
                        for n in c["nodes"]:
                            if n["role"] != "PRIMARY":
                                faulted_node_name = n["name"]
                                break

            if faulted_node_name and faulted_node_name in current_node_states:
                current_node_states[faulted_node_name]["role"] = "PARTED"
                current_node_states[faulted_node_name]["state"] = "OFFLINE"
                current_node_states[faulted_node_name]["is_write"] = False
                for c in base_clusters:
                    if any(n["name"] == faulted_node_name for n in c["nodes"]):
                        current_cluster_states[c["name"]] = "VALID_DEGRADED"
                        break

            event_desc = "🚨 达到阈值确认故障屏蔽：持续停机达到阈值，节点彻底剔除，只读路由切断，Site 降级为 VALID_DEGRADED"
        elif is_recovery:
            event_type = "recovery"
            recovered_node_name = ""
            started_matches = re.findall(r"(?:start|启动|拉起|重新准入|恢复)[^\n,]*?([a-zA-Z0-9_]+)", st_text)

            for sm in started_matches:
                for cand_n in all_node_names:
                    if cand_n == sm:
                        recovered_node_name = cand_n
                        break
                if recovered_node_name:
                    break

            if not recovered_node_name:
                sorted_by_len = sorted(all_node_names, key=len, reverse=True)
                for sm in started_matches:
                    for cand_n in sorted_by_len:
                        if cand_n in sm or sm in cand_n:
                            recovered_node_name = cand_n
                            break
                    if recovered_node_name:
                        break

            if not recovered_node_name:
                for n_n, n_st in current_node_states.items():
                    if n_st["role"] == "PARTED" or n_st["state"] == "OFFLINE":
                        recovered_node_name = n_n
                        break

            if recovered_node_name and recovered_node_name in current_node_states:
                is_pri = recovered_node_name in primary_nodes
                current_node_states[recovered_node_name]["role"] = "PRIMARY" if is_pri else "STANDBY"
                current_node_states[recovered_node_name]["state"] = "ACTIVE"
                current_node_states[recovered_node_name]["is_write"] = is_pri
                for c in base_clusters:
                    if any(n["name"] == recovered_node_name for n in c["nodes"]):
                        current_cluster_states[c["name"]] = "VALID"
                        break

            event_desc = "🔄 持续成功达到阈值确认恢复：探测成功达标，节点自动重新准入只读候选，读路由与流复制恢复"
        else:
            event_type = "normal"
            event_desc = "📊 路由基线检查：控制台查询路由分配，主库承接写流量，从库准入只读候选"

        # 用累积状态覆盖基础拓扑，生成本步骤的快照
        snapshot_clusters = []
        for c in base_clusters:
            c_name = c["name"]
            c_nodes = []
            for n in c["nodes"]:
                n_name = n["name"]
                n_st = current_node_states.get(n_name, {})
                c_nodes.append({
                    **n,
                    "role": n_st.get("role", n.get("role", "STANDBY")),
                    "state": n_st.get("state", n.get("state", "ACTIVE")),
                    "is_write": n_st.get("is_write", n.get("is_write", False)),
                })
            snapshot_clusters.append({
                **c,
                "state": current_cluster_states.get(c_name, c.get("state", "VALID")),
                "nodes": c_nodes,
            })

        # 各 Site 第一个非主节点的在线状态，用于驱动读流量/WAL 连线的动画开关
        std_a_active = True
        if len(snapshot_clusters) > 0:
            std_a = next((n for n in snapshot_clusters[0]["nodes"] if n["role"] != "PRIMARY"), None)
            if std_a and (std_a.get("role") == "PARTED" or std_a.get("state") == "OFFLINE"):
                std_a_active = False

        std_b_active = True
        if len(snapshot_clusters) > 1:
            std_b = next((n for n in snapshot_clusters[1]["nodes"] if n["role"] != "PRIMARY"), None)
            if std_b and (std_b.get("role") == "PARTED" or std_b.get("state") == "OFFLINE"):
                std_b_active = False

        # 生成本步骤中"代理视角"的感知描述（拓扑状态、监控计数、路由决策）
        top_state_a = current_cluster_states.get("site_a", "VALID")
        top_state_b = current_cluster_states.get("site_b", "VALID")
        top_desc = f"Site A: {top_state_a} | Site B: {top_state_b}"

        if idx == 0:
            proxy_state = {
                "status": "HEALTHY",
                "badge": "探活就绪",
                "perception_title": "探活就绪 (4/4 正常)",
                "monitor_status": "4 节点探活在线 (周期 2s, 阈值 3次)",
                "topology_status": top_desc,
                "routing_decision": "写->A0(10011) | 读->A1(10012)",
                "details": "fbasecman 启动完成，探活引擎全覆盖，双中心拓扑已发布",
            }
        elif is_debounce:
            proxy_state = {
                "status": "DEBOUNCING",
                "badge": "探活防抖 (1/3次)",
                "perception_title": "感知瞬断 · 防抖保护中",
                "monitor_status": "A1 探测失败 1 次 (< 阈值 3 次)",
                "topology_status": top_desc + " (保持)",
                "routing_decision": "保持 A1 候选 (未达阈值不屏蔽)",
                "details": "fbasecman 探活防抖生效：检测到 A1 瞬断，未达连续 3 次失败阈值，网关主动抑制误屏蔽，保持只读候选！",
            }
        elif is_fault:
            proxy_state = {
                "status": "DEGRADED",
                "badge": "感知故障 · 集群降级",
                "perception_title": "3次探测失败超限 · 确认故障",
                "monitor_status": "A1 连续 3 次探测失败 (达到阈值)",
                "topology_status": top_desc + " (⚠️ 降级)",
                "routing_decision": "从 qa_rep 彻底剔除 A1 读候选 | 读路由切断/回退",
                "details": "fbasecman 感知从库持续宕机满 3 次：主动将 site_a 拓扑降级为 VALID_DEGRADED，并从路由表彻底剔除 A1！",
            }
        elif is_recovery:
            proxy_state = {
                "status": "RECOVERED",
                "badge": "感知恢复 · 重新准入",
                "perception_title": "3次探测成功达标 · 确认恢复",
                "monitor_status": "A1 连续 3 次探测成功 (ONLINE)",
                "topology_status": top_desc + " (✨ 恢复)",
                "routing_decision": "A1 重新准入 qa_rep 只读候选",
                "details": "fbasecman 感知从库恢复并连续探测成功满 3 次：主动将 site_a 拓扑恢复为 VALID，并将 A1 重新准入只读候选！",
            }
        else:
            proxy_state = {
                "status": "ROUTING",
                "badge": "读写分流",
                "perception_title": "基线确认 · 正常分流",
                "monitor_status": "全部节点 ONLINE, 0次失败",
                "topology_status": top_desc,
                "routing_decision": "写->A0 (is_write=true) | 读->A1 (AVAILABLE)",
                "details": "fbasecman 确认初始路由分配：A0 承接写，A1 准入只读候选",
            }

        snapshots.append({
            "step_index": idx,
            "step_num": idx + 1,
            "title": title,
            "action": action,
            "expected": expected,
            "actual": actual,
            "event_type": event_type,
            "event_desc": event_desc,
            "clusters": snapshot_clusters,
            "proxy_state": proxy_state,
            "links": {
                "write_a": True,
                "read_a": std_a_active,
                "read_b": std_b_active,
                "wal_a": std_a_active,
                "wal_b": std_b_active,
                "mmr": True,
            },
        })

    return snapshots


def extract_topology(raw_text: str, suite_name: str,
                     steps: Optional[List[Dict[str, Any]]] = None,
                     config_dirs: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
    """从报告文本中动态提取高可用集群拓扑（节点、角色、链路），供拓扑视图渲染。"""
    tables = parse_psql_tables(raw_text)
    cfg = load_runtime_config(*(config_dirs or []))
    db_ports = cfg.get("database", {}).get("ports", {})
    fbasecman_cfg = cfg.get("fbasecman", {})
    proxy_write_port = fbasecman_cfg.get("write_port", 17432)
    proxy_read_port = fbasecman_cfg.get("read_port", 16432)

    clusters: Dict[str, Dict[str, Any]] = {}
    node_monitors: Dict[str, Dict[str, str]] = {}
    node_routings: Dict[str, Dict[str, str]] = {}

    for headers, rows in tables:
        if "cluster_name" in headers and "nodes" in headers and "current_primary" in headers:
            for r in rows:
                c_name = r.get("cluster_name", "").strip()
                if not c_name:
                    continue
                state = r.get("topology_state", "VALID").strip()
                pri = r.get("current_primary", "").strip()
                nodes = [n.strip() for n in r.get("nodes", "").split(",") if n.strip()]
                if c_name not in clusters:
                    clusters[c_name] = {"name": c_name, "state": state, "primary": pri, "nodes": {}}
                else:
                    clusters[c_name]["state"] = state
                    if pri and pri != "NULL":
                        clusters[c_name]["primary"] = pri
                for n in nodes:
                    if n not in clusters[c_name]["nodes"]:
                        clusters[c_name]["nodes"][n] = {
                            "name": n,
                            "role": "PRIMARY" if n == pri else "STANDBY",
                            "state": "ACTIVE",
                        }

        elif "node_name" in headers and "endpoint_id" in headers:
            for r in rows:
                n_name = r.get("node_name", "").strip()
                if n_name:
                    node_monitors[n_name] = r

        elif "candidate_node" in headers and ("is_write_target" in headers or "candidate_type" in headers):
            for r in rows:
                cand = r.get("candidate_node", "").strip()
                if not cand:
                    continue
                node_routings[cand] = r
                c_name = r.get("cluster_name", "").strip()
                pri = r.get("current_primary", "").strip()
                if c_name and c_name not in clusters:
                    clusters[c_name] = {"name": c_name, "state": "VALID", "primary": pri, "nodes": {}}
                if c_name:
                    clusters[c_name]["nodes"][cand] = {
                        "name": cand,
                        "role": "PRIMARY" if (r.get("is_write_target") == "true" or "primary" in r.get("effective_grouprole", "").lower()) else "STANDBY",
                        "state": r.get("effective_state", "active").upper(),
                        "is_write": r.get("is_write_target") == "true",
                    }

    # 兼容列表形式的状态行：'- cluster_name: topology_state=..., current_primary=...'
    for m in re.finditer(r"-\s*([a-zA-Z0-9_-]+):\s*topology_state=([A-Z_]+),\s*current_primary=([a-zA-Z0-9_-]+)", raw_text):
        c_name, state, pri = m.group(1), m.group(2), m.group(3)
        if c_name not in clusters:
            clusters[c_name] = {"name": c_name, "state": state, "primary": pri, "nodes": {}}
        else:
            clusters[c_name]["state"] = state
            if pri and pri != "NULL":
                clusters[c_name]["primary"] = pri

    if not clusters:
        return None

    # 汇总节点信息：主库排前面，标签按 Site A/B/C + 序号生成（A0、A1…）
    sorted_cluster_names = sorted(clusters.keys())
    cluster_list = []
    for c_idx, c_name in enumerate(sorted_cluster_names):
        c_data = clusters[c_name]
        c_letter = chr(ord('A') + c_idx) if c_idx < 26 else str(c_idx + 1)
        node_list = []
        raw_nodes = list(c_data["nodes"].values())
        raw_nodes.sort(key=lambda x: (0 if x.get("name") == c_data.get("primary") or x.get("role") == "PRIMARY" else 1, x.get("name", "")))

        for n_idx, n_info in enumerate(raw_nodes):
            n_name = n_info["name"]
            label = f"{c_letter}{n_idx}"
            mon = node_monitors.get(n_name, {})
            rt = node_routings.get(n_name, {})

            # 端口优先取监控表的 endpoint_id，缺失时按节点名在配置端口表里模糊匹配
            endpoint_id = mon.get("endpoint_id", "")
            host = ""
            port = "-"
            if ":" in endpoint_id:
                host, _, p_str = endpoint_id.partition(":")
                port = p_str.strip()
            if port == "-":
                for k, v in db_ports.items():
                    if isinstance(v, (int, str)) and (k == n_name or k in n_name or n_name in k):
                        port = str(v)
                        break

            role = n_info.get("role", "STANDBY")
            if n_name == c_data.get("primary") and c_data.get("primary") != "NULL":
                role = "PRIMARY"
            state = n_info.get("state", "ACTIVE")
            is_write = (role == "PRIMARY") or (rt.get("is_write_target") == "true")

            node_list.append({
                "name": n_name,
                "label": label,
                "cluster": c_name,
                "host": host or "127.0.0.1",
                "port": port,
                "role": role,
                "state": state,
                "is_write": is_write,
                "fault_count": mon.get("fault_count", "0"),
                "observed_role": mon.get("observed_role", role.lower()),
                "fault_flags": mon.get("fault_flags", "{}"),
            })

        cluster_list.append({
            "name": c_name,
            "label": f"Site {c_letter} ({c_name})",
            "state": c_data.get("state", "VALID"),
            "primary": c_data.get("primary", "-"),
            "nodes": node_list,
        })

    step_snapshots = []
    if steps:
        step_snapshots = build_step_topology_snapshots(raw_text, cluster_list, steps, proxy_write_port, proxy_read_port)

    return {
        "has_topology": len(cluster_list) > 0,
        "proxy": {
            "write_port": proxy_write_port,
            "read_port": proxy_read_port,
            "status": "ACTIVE",
        },
        "clusters": cluster_list,
        "step_snapshots": step_snapshots,
    }


def parse_report(target: str, root_dir, *, config_dirs: Optional[List[Any]] = None) -> Dict[str, Any]:
    """把 report.txt 及同目录工件解析成结构化数据，供报告弹窗渲染。"""
    suite_name, _, case_name = target.partition(".")
    if not case_name:
        suite_name = target
        case_name = target

    case_dir = Path(root_dir)
    report_file = case_dir / "report.txt"

    if not report_file.exists():
        return {
            "found": False,
            "target": target,
            "error": f"未找到测试报告文件: {case_dir}/report.txt",
        }

    raw_text = report_file.read_text(encoding="utf-8", errors="replace")

    def find_field(pat, default=""):
        m = re.search(pat, raw_text, re.M)
        return m.group(1).strip() if m else default

    status = find_field(r"^(?:结论|Status):\s*(PASS|FAIL)", "UNKNOWN")
    start_time = find_field(r"^测试开始时间:\s*(.*)$")
    end_time = find_field(r"^测试结束时间:\s*(.*)$")
    reason = find_field(r"^(?:通过原因|失败原因):\s*(.*)$")

    purpose = ""
    purp_match = re.search(r"^验证目的:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if purp_match:
        purpose = purp_match.group(1).strip()

    purpose_source = "execution_report"
    archived = None
    result_path = case_dir / "result.json"
    try:
        result_fact = json.loads(result_path.read_text(encoding="utf-8"))
        from platform_regress.reporting.description import read_description
        archived = read_description(case_dir, str(result_fact.get("execution_id") or ""))
        if archived and archived.get("target") == target and archived.get("purpose"):
            purpose = archived["purpose"]
            purpose_source = "execution_description"
    except (OSError, ValueError, TypeError):
        pass
    if not purpose or purpose == target:
        try:
            catalog = json.loads((Path(__file__).parents[1] / "regression" / "catalog.json").read_text(encoding="utf-8"))
            item = next((item for item in catalog["cases"] if item["target"] == target), {})
            purpose = item.get("summary") or purpose
            purpose_source = "current_catalog"
        except (OSError, ValueError, KeyError, TypeError):
            pass

    execution_scope = None
    execution_scope_source = "current_catalog"
    try:
        catalog = json.loads((Path(__file__).parents[1] / "regression" / "catalog.json").read_text(encoding="utf-8"))
        item = next((item for item in catalog["cases"] if item["target"] == target), {})
        execution_scope = item.get("execution_scope")
        if archived and archived.get("target") == target and archived.get("execution_scope"):
            execution_scope = archived["execution_scope"]
            execution_scope_source = "execution_description"
    except (OSError, ValueError, KeyError, TypeError):
        pass

    if isinstance(execution_scope, dict) and isinstance(execution_scope.get("transactions"), str):
        execution_scope = {**execution_scope, "transactions": [{
            "stage": "协议与事务边界", "operation": execution_scope["transactions"],
            "boundary": "按当次记录的 BEGIN/COMMIT/ROLLBACK 与 Q/Sync 收口判定",
        }]}

    test_contents = []
    cont_match = re.search(r"^测试内容:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if cont_match:
        for line in cont_match.group(1).splitlines():
            line = line.strip()
            if line:
                test_contents.append(line)

    key_config = ""
    cfg_match = re.search(r"^关键配置:\s*\n(.*?)(?=\n\n[^\s]|\n[^\s]+:|\Z)", raw_text, re.S | re.M)
    if cfg_match:
        key_config = cfg_match.group(1).strip()

    # 步骤块：'步骤 N: 标题' 到下一个 步骤/检测项/状态转换 之间
    steps = []
    step_blocks = re.findall(r"(步骤\s*\d+:[^\n]+)(.*?)(?=(?:步骤\s*\d+:|检测项\s*\d+:|===\s*状态转换|\Z))", raw_text, re.S)
    for title, body in step_blocks:
        step_obj = {
            "title": title.strip(),
            "status": "PASS",
            "action": "",
            "command": "",
            "expected": "",
            "actual": "",
            "evidence": "",
            "state_table": "",
        }
        if re.search(r"判定:\s*FAIL", body):
            step_obj["status"] = "FAIL"

        act_m = re.search(r"动作:\s*(.*?)(?=\n\s*(?:关键期望|预期|期望|实际|判定|证据|日志证据|中间状态|结果分析|判定依据)|\Z)", body, re.S)
        if act_m:
            step_obj["action"] = act_m.group(1).strip()

        cmd_m = re.search(r"(?:实际执行|执行内容|命令):\s*(.*?)(?=\n\s*(?:中间状态|证据|日志证据|动作|关键期望|预期|期望|实际|判定|结果分析|判定依据)|\Z)", body, re.S)
        if cmd_m:
            raw_cmd = cmd_m.group(1).strip()
            cleaned_lines = []
            for line in raw_cmd.splitlines():
                sline = line.strip()
                if sline.startswith("监听端口:"):
                    step_obj["port"] = sline.partition(":")[2].strip()
                elif sline.startswith("配置文件:"):
                    step_obj["config_file"] = sline.partition(":")[2].strip()
                elif sline.startswith("关键期望:") or sline.startswith("预期:") or sline.startswith("期望:"):
                    if not step_obj["expected"]:
                        step_obj["expected"] = sline.partition(":")[2].strip()
                else:
                    cleaned_lines.append(line)
            cleaned_cmd = "\n".join(cleaned_lines).strip()
            if cleaned_cmd.startswith("$ "):
                parts = re.split(r"\n\s*\n", cleaned_cmd, maxsplit=1)
                if len(parts) == 2:
                    step_obj["command"] = parts[0].strip()
                    if not step_obj["state_table"]:
                        step_obj["state_table"] = parts[1].strip()
                else:
                    step_obj["command"] = cleaned_cmd
            else:
                step_obj["command"] = cleaned_cmd

        exp_m = re.search(r"(?:关键期望|预期|期望):\s*(.*?)(?=\n\s*(?:实际|判定|证据|日志证据|动作|结果分析|判定依据)|\Z)", body, re.S)
        if exp_m:
            step_obj["expected"] = exp_m.group(1).strip()

        actu_m = re.search(r"实际(?:输出)?:\s*(.*?)(?=\n\s*(?:判定|证据|日志证据|关键期望|预期|期望|结果分析|判定依据)|\Z)", body, re.S)
        if actu_m:
            step_obj["actual"] = actu_m.group(1).strip()

        ana_m = re.search(r"(?:结果分析|判定依据):\s*(.*?)(?=\n\s*(?:判定|证据|日志证据|关键期望|预期|期望)|\Z)", body, re.S)
        if ana_m:
            step_obj["analysis"] = ana_m.group(1).strip()

        evi_m = re.search(r"(?:日志证据|证据):\s*(.*?)(?=\n\s*(?:动作|关键期望|预期|期望|实际|判定|结果分析|判定依据)|\Z)", body, re.S)
        if evi_m:
            step_obj["evidence"] = evi_m.group(1).strip()

        tbl_m = re.search(r"中间状态:\s*(.*?)(?=\n\s*(?:证据|日志证据|动作|关键期望|预期|期望|实际|判定|结果分析|判定依据)|\Z)", body, re.S)
        if tbl_m:
            step_obj["state_table"] = tbl_m.group(1).strip()

        steps.append(step_obj)

    # 断言块：'检测项 N: 标题' 段落，只有 预期/实际/判定 三个字段
    assertions = []
    assert_blocks = re.findall(r"(检测项\s*\d+:[^\n]+)(.*?)(?=(?:检测项\s*\d+:|步骤\s*\d+:|===\s*状态转换|\Z))", raw_text, re.S)
    for title, body in assert_blocks:
        st = "PASS"
        if re.search(r"判定:\s*FAIL", body):
            st = "FAIL"
        exp_m = re.search(r"预期:\s*(.*?)(?=\n\s*(?:实际|判定)|\Z)", body, re.S)
        actu_m = re.search(r"实际:\s*(.*?)(?=\n\s*(?:判定)|\Z)", body, re.S)
        assertions.append({
            "title": title.strip(),
            "status": st,
            "expected": exp_m.group(1).strip() if exp_m else "",
            "actual": actu_m.group(1).strip() if actu_m else "",
        })

    for step in steps:
        clean = re.sub(r"^步骤\s*\d+[:：]\s*", "", step["title"])
        if re.sub(r"^步骤\s*\d+[:：]\s*", "", step["title"]).startswith(("执行 SQL", "执行命令")):
            step["intent"] = "action"
        else:
            if any(marker in clean for marker in ("启动 fbasecman", "探活初始化", "数据同步基线",
                                                  "环境收尾", "失败现场诊断", "清理", "恢复原始")):
                step["intent"] = "cleanup" if any(marker in clean for marker in ("环境收尾", "失败现场诊断", "清理")) else "action"

        # Older ha_commands reports were written before the structured event
        # fields (analysis/intent/assertion) were persisted. Keep those
        # historical reports readable with explicit transport semantics rather
        # than showing a bare PASS or an empty comparison.
        if step.get("intent") in ("action", "cleanup") or clean.startswith(("查看", "执行 SQL", "执行命令")):
            step["intent"] = step.get("intent") or "action"
            step.setdefault("expected", "执行记录已保存；业务结果由后续状态验证步骤判断")
            step.setdefault("actual", step.get("command") or "执行完成")
            step.setdefault("analysis", "这是执行/准备记录，不单独证明产品功能通过。")
        elif (step.get("expected") and step.get("actual")) or clean.startswith(("核对", "验证", "确认")):
            step["intent"] = step.get("intent") or "verify"
            # Missing historical expectations or comparison rules remain
            # missing. A title and PASS label cannot reconstruct an assertion.

    # SDK 事件是同次执行的结构化证据；补回旧文本遗漏的业务步骤。
    from platform_regress.reporting.case_report import _event_steps, _steps_payload
    recorded = _steps_payload(target, _event_steps(case_dir))["steps"]
    matched_steps = set()
    for fact in recorded:
        clean_title = re.sub(r"^(?:步骤|检测项)\s*\d+[:：]\s*", "", fact["title"])
        match = next((step for step in steps if id(step) not in matched_steps and re.sub(
            r"^(?:步骤|检测项)\s*\d+[:：]\s*", "", step["title"]) == clean_title), None)
        mapped = {**fact, "status": fact["result"], "title": clean_title}
        if match is not None:
            matched_steps.add(id(match))
            match.update({key: value for key, value in mapped.items() if value is not None})
        elif fact.get("intent") == "verify" or fact.get("assertion") or not clean_title.startswith("执行"):
            steps.append(mapped)

    if any(target.startswith(f"guc.{group}_") for group in
           ("extended_boundary", "transaction_sync", "savepoint_report", "backend_redeploy")):
        # Coverage is archived execution data, including configurations that
        # could not start. Show those branches without inventing a run.
        coverage_files = list(case_dir.glob('artifacts/*/guc-alignment-coverage.json'))
        if len(coverage_files) == 1:
            recorded_keys = {step.get('actual', {}).get('key') for step in steps
                             if isinstance(step.get('actual'), dict) and '子场景结论' in step.get('title', '')}
            try:
                coverage = json.loads(coverage_files[0].read_text())
            except (OSError, ValueError):
                coverage = {}
            mode = target.rsplit('_', 2)[-1] if target.endswith('_hint') else 'sql_parse'
            for row in coverage.get('checks', []):
                if not isinstance(row, dict) or row.get('key') in recorded_keys or not row.get('scenario'):
                    continue
                steps.append({'title': f"{row.get('topology', '')}/{mode}/{row['scenario']} 子场景结论",
                              'intent': 'verify', 'status': row.get('status', 'UNKNOWN'),
                              'expected': '以本次归档的检查计划为准', 'actual': row,
                              'analysis': row.get('reason', '结论来自本次覆盖记录；具体取值见归档证据')})
        steps = _present_guc_alignment_steps(steps)

    # Some suite executors persist expected/actual but omit a human analysis
    # field. Preserve the factual comparison without inventing a new verdict.
    for step in steps:
        if (step.get("status") in ("PASS", "FAIL") and step.get("expected") and
                step.get("actual") and not step.get("analysis") and
                step.get("intent") != "action"):
            step["analysis"] = "已将本步骤保存的实际结果与声明期望进行核对。"

    from products.fbasecman.reports.observations import readable_jdbc_observations
    for step in steps:
        for field in ("expected", "actual", "action", "command", "state_table", "analysis"):
            value = step.get(field)
            if not isinstance(value, str):
                step[f"structured_{field}"] = value
                step[field] = _display_report_value(value)
        step["actual"] = readable_jdbc_observations(step.get("actual"))

    # 若用例判定为 FAIL 但步骤中未包含 FAIL 步骤（例如执行中抛出异常提前退出导致报告中断），从 steps.json 或 reason 补全
    if status == "FAIL" and not any(s.get("status") == "FAIL" for s in steps):
        steps_file = case_dir / "steps.json"
        if steps_file.is_file():
            try:
                jdata = json.loads(steps_file.read_text(encoding="utf-8"))
                for js in jdata.get("steps", []):
                    if js.get("result") == "FAIL" or js.get("status") == "FAIL":
                        if any(s["title"] == js.get("title") for s in steps):
                            continue
                        cmd = ""
                        state_tbl = ""
                        for ex in js.get("execution", []):
                            if isinstance(ex, dict) and "text" in ex:
                                t = ex["text"]
                                if "\n\n" in t:
                                    parts = t.split("\n\n", 1)
                                    cmd = parts[0]
                                    state_tbl = parts[1]
                                elif t.startswith("$"):
                                    cmd = t
                                else:
                                    state_tbl = t
                                break
                        fail_step = {
                            "title": js.get("title", f"步骤 {len(steps) + 1}: 失败步骤"),
                            "status": "FAIL",
                            "action": "",
                            "command": cmd,
                            "expected": js.get("expected", ""),
                            "actual": js.get("actual", reason or "未达到预期"),
                            "evidence": "",
                            "state_table": state_tbl,
                        }
                        steps.append(fail_step)
                        if not any(a.get("status") == "FAIL" for a in assertions):
                            assertions.append({
                                "title": f"检测项 {len(assertions) + 1}: {js.get('title', '步骤断言')}",
                                "status": "FAIL",
                                "expected": js.get("expected", ""),
                                "actual": state_tbl.strip() or js.get("actual", reason or ""),
                            })
            except Exception:
                pass
        if not any(s.get("status") == "FAIL" for s in steps) and reason:
            steps.append({
                "title": f"步骤 {len(steps) + 1}: 断言未通过中断",
                "status": "FAIL",
                "action": "",
                "command": "",
                "expected": "用例执行完成",
                "actual": reason,
                "evidence": "",
                "state_table": "",
            })
            if not any(a.get("status") == "FAIL" for a in assertions):
                assertions.append({
                    "title": f"检测项 {len(assertions) + 1}: 用例执行状态",
                    "status": "FAIL",
                    "expected": "PASS",
                    "actual": f"FAIL ({reason})",
                })


    # 收集用例目录下可下载的日志文件
    logs = []
    backtrace = ""
    bt_file = case_dir / "backtrace.txt"
    if bt_file.exists():
        try:
            backtrace = bt_file.read_text(encoding="utf-8", errors="replace").strip()
            logs.append("backtrace.txt")
        except Exception:
            pass

    if (case_dir / "fbasecman.log").exists():
        logs.append("fbasecman.log")
    logs_sub = case_dir / "logs"
    if logs_sub.is_dir():
        for lf in sorted(logs_sub.glob("*.log")):
            logs.append(f"logs/{lf.name}")

    topology = extract_topology(raw_text, suite_name, steps=steps,
                                config_dirs=config_dirs or [case_dir])

    return {
        "found": True,
        "target": target,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "reason": reason,
        "purpose": purpose,
        "purpose_source": purpose_source,
        "execution_scope": execution_scope,
        "execution_scope_source": execution_scope_source,
        "test_contents": test_contents,
        "key_config": key_config,
        "steps": steps,
        "assertions": assertions,
        "available_logs": logs,
        "backtrace": backtrace,
        "topology": topology,
        "raw_text": raw_text,
    }
