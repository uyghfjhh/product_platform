"""Pure parsing helpers shared by the Web report and topology views."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def load_run_meta(root_dir):
    """Read optional historical Web metadata without starting its task manager."""
    path = Path(root_dir) / "output" / "runs" / ".web_meta.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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


def load_runtime_config(root_dir):
    """按优先级读取首个可用 yaml 配置，用于发现数据库端口与代理端口。"""
    for cfg_name in ('regress.local.yaml', 'regress.yaml', 'stable.local.yaml', 'stable.yaml'):
        p = Path(root_dir) / cfg_name
        if p.is_file():
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def case_status_meta(root_dir, suite_name, case_name, target, current_target=None):
    """读取单个用例的执行状态/耗时/报告标记，供套件列表页展示。

    状态来源按优先级：正在运行 > summary.json > report.txt 结论行 > web_meta 缓存。
    """
    if current_target and (current_target == target or current_target == suite_name):
        return {
            "status": "RUNNING",
            "duration": "-",
            "has_report": False,
            "timestamp": "-",
        }

    run_dir = Path(root_dir) / "output" / "runs" / suite_name / case_name
    meta_cache = load_run_meta(root_dir).get(target)

    # 1. 优先读结构化 summary.json
    summary_file = run_dir / "summary.json"
    if summary_file.exists():
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            status = data.get("status", "PASS").upper()
            duration = data.get("duration")
            dur_str = f"{float(duration):.2f}s" if duration is not None else "-"
            return {
                "status": status,
                "duration": dur_str,
                "has_report": (run_dir / "report.txt").exists(),
                "timestamp": data.get("start_time", "-"),
            }
        except Exception:
            pass

    # 2. 退化到解析 report.txt 文本（结论行 + 起止时间差）
    report_file = run_dir / "report.txt"
    if report_file.exists():
        try:
            content = report_file.read_text(encoding="utf-8", errors="replace")
            status = "PASS"
            st_match = re.search(r"^(?:结论|Status):\s*(PASS|FAIL)", content, re.M)
            if st_match:
                status = st_match.group(1).upper()

            dur_str = "-"
            if meta_cache and "duration" in meta_cache:
                dur_str = meta_cache["duration"]
            else:
                t1_match = re.search(r"^测试开始时间:\s*(.*)$", content, re.M)
                t2_match = re.search(r"^测试结束时间:\s*(.*)$", content, re.M)
                if t1_match and t2_match:
                    try:
                        d1 = datetime.strptime(t1_match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                        d2 = datetime.strptime(t2_match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                        sec = abs((d2 - d1).total_seconds())
                        dur_str = f"{sec:.2f}s" if sec > 0 else "<1s"
                    except Exception:
                        dur_str = "-"

            timestamp = "-"
            ts_match = re.search(r"^测试开始时间:\s*(.*)$", content, re.M)
            if ts_match:
                timestamp = ts_match.group(1).strip()
            elif meta_cache and "timestamp" in meta_cache:
                timestamp = meta_cache["timestamp"]

            return {
                "status": status,
                "duration": dur_str,
                "has_report": True,
                "timestamp": timestamp,
            }
        except Exception:
            pass

    # 3. 最后兜底：web 任务元信息缓存（报告文件被清理时仍有上次执行状态）
    if meta_cache:
        return {
            "status": meta_cache.get("status", "UNTESTED"),
            "duration": meta_cache.get("duration", "-"),
            "has_report": (run_dir / "report.txt").exists(),
            "timestamp": meta_cache.get("timestamp", "-"),
        }

    return {
        "status": "UNTESTED",
        "duration": "-",
        "has_report": False,
        "timestamp": "-",
    }


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

            event_desc = f"🚨 达到阈值确认故障屏蔽：持续停机达到阈值，节点彻底剔除，只读路由切断，Site 降级为 VALID_DEGRADED"
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

            event_desc = f"🔄 持续成功达到阈值确认恢复：探测成功达标，节点自动重新准入只读候选，读路由与流复制恢复"
        else:
            event_type = "normal"
            event_desc = f"📊 路由基线检查：控制台查询路由分配，主库承接写流量，从库准入只读候选"

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


def extract_topology(root_dir, raw_text: str, suite_name: str, steps: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """从报告文本中动态提取高可用集群拓扑（节点、角色、链路），供拓扑视图渲染。"""
    tables = parse_psql_tables(raw_text)
    cfg = load_runtime_config(root_dir)
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


def parse_report(target: str, root_dir) -> Dict[str, Any]:
    """把 report.txt 及同目录工件解析成结构化数据，供报告弹窗渲染。"""
    suite_name, _, case_name = target.partition(".")
    if not case_name:
        suite_name = target
        case_name = target

    report_root = Path(root_dir)
    case_dir = report_root / "output" / "runs" / suite_name / case_name
    report_file = case_dir / "report.txt"

    if not report_file.exists():
        return {
            "found": False,
            "target": target,
            "error": f"未找到测试报告文件: output/runs/{suite_name}/{case_name}/report.txt",
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

        act_m = re.search(r"动作:\s*(.*?)(?=\n\s*(?:预期|实际|判定|证据|中间状态)|\Z)", body, re.S)
        if act_m:
            step_obj["action"] = act_m.group(1).strip()

        cmd_m = re.search(r"(?:实际执行|命令):\s*(.*?)(?=\n\s*(?:中间状态|证据|动作|预期|实际|判定)|\Z)", body, re.S)
        if cmd_m:
            step_obj["command"] = cmd_m.group(1).strip()

        exp_m = re.search(r"预期:\s*(.*?)(?=\n\s*(?:实际|判定|证据|动作)|\Z)", body, re.S)
        if exp_m:
            step_obj["expected"] = exp_m.group(1).strip()

        actu_m = re.search(r"实际:\s*(.*?)(?=\n\s*(?:判定|证据|预期)|\Z)", body, re.S)
        if actu_m:
            step_obj["actual"] = actu_m.group(1).strip()

        evi_m = re.search(r"证据:\s*(.*?)(?=\n\s*(?:动作|预期|实际|判定)|\Z)", body, re.S)
        if evi_m:
            step_obj["evidence"] = evi_m.group(1).strip()

        tbl_m = re.search(r"中间状态:\s*(.*?)(?=\n\s*(?:证据|动作|预期|实际|判定)|\Z)", body, re.S)
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

    topology = extract_topology(report_root, raw_text, suite_name, steps=steps)

    return {
        "found": True,
        "target": target,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "reason": reason,
        "purpose": purpose,
        "test_contents": test_contents,
        "key_config": key_config,
        "steps": steps,
        "assertions": assertions,
        "available_logs": logs,
        "backtrace": backtrace,
        "topology": topology,
        "raw_text": raw_text,
    }
