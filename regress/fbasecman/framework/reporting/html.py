"""Self-contained, interactive single-file HTML test report generator for fbasecman regression."""

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from framework.reporting.junit import collect_results_from_runs


def generate_html_report(
    results: List[Dict[str, Any]],
    output_file: Optional[Path] = None,
    title: str = "fbasecman 回归测试执行报告",
) -> str:
    """Generate a self-contained, interactive modern HTML report from test result dicts."""
    total_tests = len(results)
    total_passed = sum(1 for r in results if r.get("status") == "PASS")
    total_failed = sum(1 for r in results if r.get("status") == "FAIL")
    total_errors = sum(1 for r in results if r.get("status") == "ERROR")
    total_skipped = sum(1 for r in results if r.get("status") in ("SKIPPED", "UNTESTED"))
    total_duration = sum(float(r.get("duration") or 0.0) for r in results)

    pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0.0

    # Group results by suite
    suites_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        s_name = r.get("suite", "default")
        suites_map.setdefault(s_name, []).append(r)

    suite_summaries = []
    for s_name, c_list in sorted(suites_map.items()):
        s_pass = sum(1 for c in c_list if c.get("status") == "PASS")
        s_fail = sum(1 for c in c_list if c.get("status") == "FAIL")
        s_err = sum(1 for c in c_list if c.get("status") == "ERROR")
        s_skip = sum(1 for c in c_list if c.get("status") in ("SKIPPED", "UNTESTED"))
        s_dur = sum(float(c.get("duration") or 0.0) for c in c_list)
        s_rate = (s_pass / len(c_list) * 100) if c_list else 0.0
        suite_summaries.append({
            "name": s_name,
            "total": len(c_list),
            "passed": s_pass,
            "failed": s_fail,
            "errors": s_err,
            "skipped": s_skip,
            "duration": s_dur,
            "rate": s_rate,
        })

    # Prepare JSON data for client-side interactivity
    results_json = json.dumps(results, ensure_ascii=False)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Circumference for radius 45 is 2 * pi * 45 ≈ 282.74
    circumference = 282.74
    stroke_dashoffset = circumference - (pass_rate / 100.0) * circumference

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-card: #182234;
    --bg-card-hover: #222f46;
    --border-color: #334155;
    --text-primary: #f8fafc;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-blue: #38bdf8;
    --accent-cyan: #06b6d4;
    --status-pass: #10b981;
    --status-pass-bg: rgba(16, 185, 129, 0.15);
    --status-fail: #ef4444;
    --status-fail-bg: rgba(239, 68, 68, 0.15);
    --status-skip: #f59e0b;
    --status-skip-bg: rgba(245, 158, 11, 0.15);
    --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
    padding: 24px;
  }}
  .container {{ max-width: 1300px; margin: 0 auto; }}
  
  /* Header */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 20px;
    margin-bottom: 24px;
    flex-wrap: wrap;
    gap: 16px;
  }}
  .header-left h1 {{
    font-size: 24px;
    font-weight: 700;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .header-left .timestamp {{
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 4px;
  }}
  .header-tag {{
    display: inline-block;
    background: rgba(56, 189, 248, 0.15);
    color: var(--accent-blue);
    border: 1px solid rgba(56, 189, 248, 0.3);
    padding: 4px 10px;
    border-radius: 9999px;
    font-size: 12px;
    font-weight: 600;
  }}

  /* KPI Cards Grid */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)) 180px;
    gap: 16px;
    margin-bottom: 24px;
  }}
  .kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }}
  .kpi-title {{ font-size: 13px; color: var(--text-secondary); font-weight: 500; }}
  .kpi-value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .kpi-card.pass .kpi-value {{ color: var(--status-pass); }}
  .kpi-card.fail .kpi-value {{ color: var(--status-fail); }}
  .kpi-card.skip .kpi-value {{ color: var(--status-skip); }}
  .kpi-card.dur .kpi-value {{ color: var(--accent-blue); font-size: 24px; }}

  /* Donut Chart Card */
  .donut-card {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 12px;
  }}
  .donut-svg {{ width: 90px; height: 90px; transform: rotate(-90deg); }}
  .donut-circle-bg {{ fill: none; stroke: var(--border-color); stroke-width: 10; }}
  .donut-circle-val {{
    fill: none;
    stroke: var(--status-pass);
    stroke-width: 10;
    stroke-linecap: round;
    stroke-dasharray: {circumference:.2f};
    stroke-dashoffset: {stroke_dashoffset:.2f};
    transition: stroke-dashoffset 0.8s ease-in-out;
  }}
  .donut-label {{
    margin-top: -62px;
    margin-bottom: 28px;
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
  }}
  .donut-desc {{ font-size: 12px; color: var(--text-secondary); }}

  /* Suites Breakdown Table */
  .section-title {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 12px;
    color: var(--text-primary);
  }}
  .suite-table-wrap {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    overflow-x: auto;
    margin-bottom: 24px;
  }}
  table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }}
  th {{ background: var(--bg-secondary); color: var(--text-secondary); padding: 10px 16px; font-weight: 600; border-bottom: 1px solid var(--border-color); }}
  td {{ padding: 10px 16px; border-bottom: 1px solid var(--border-color); color: var(--text-primary); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: var(--bg-card-hover); }}
  .progress-bar-bg {{ background: var(--border-color); border-radius: 4px; height: 8px; width: 100px; overflow: hidden; }}
  .progress-bar-fill {{ background: var(--status-pass); height: 100%; }}
  .progress-bar-fill.has-fail {{ background: var(--status-fail); }}

  /* Filter Controls */
  .controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 20px;
  }}
  .filter-btn-group {{ display: flex; gap: 4px; }}
  .filter-btn {{
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    color: var(--text-secondary);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    transition: all 0.2s;
  }}
  .filter-btn:hover {{ background: var(--border-color); color: var(--text-primary); }}
  .filter-btn.active {{
    background: var(--accent-blue);
    color: #000;
    font-weight: 600;
    border-color: var(--accent-blue);
  }}
  .search-input, .suite-select {{
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    color: var(--text-primary);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 13px;
    outline: none;
  }}
  .search-input:focus, .suite-select:focus {{ border-color: var(--accent-blue); }}
  .search-input {{ flex-grow: 1; min-width: 200px; }}

  /* Case List Cards */
  .cases-list {{ display: flex; flex-direction: column; gap: 10px; }}
  .case-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    transition: border-color 0.2s;
    overflow: hidden;
  }}
  .case-card:hover {{ border-color: var(--accent-blue); }}
  .case-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    cursor: pointer;
    user-select: none;
  }}
  .case-left {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .badge {{
    font-size: 11px;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .badge-pass {{ background: var(--status-pass-bg); color: var(--status-pass); border: 1px solid rgba(16, 185, 129, 0.3); }}
  .badge-fail {{ background: var(--status-fail-bg); color: var(--status-fail); border: 1px solid rgba(239, 68, 68, 0.3); }}
  .badge-skip {{ background: var(--status-skip-bg); color: var(--status-skip); border: 1px solid rgba(245, 158, 11, 0.3); }}
  .case-target {{ font-family: var(--font-mono); font-weight: 600; font-size: 14px; color: var(--text-primary); }}
  .case-msg {{ color: var(--status-fail); font-size: 13px; margin-left: 8px; }}
  .case-dur {{ font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); }}
  .toggle-icon {{ color: var(--text-muted); font-size: 14px; margin-left: 8px; }}

  /* Case Details Drawer */
  .case-details {{
    border-top: 1px solid var(--border-color);
    background: #111827;
    padding: 16px;
    display: none;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    max-height: 500px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    color: #e2e8f0;
  }}
  .case-card.open .case-details {{ display: block; }}
  .case-card.open .toggle-icon {{ transform: rotate(180deg); }}

  /* Empty state */
  .empty-state {{
    text-align: center;
    padding: 40px;
    color: var(--text-muted);
    font-size: 14px;
  }}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <h1>🚀 {html.escape(title)}</h1>
      <div class="timestamp">生成时间: {timestamp} | fbasecman Regression Framework v2</div>
    </div>
    <div>
      <span class="header-tag">全自动化可视化诊断报告</span>
    </div>
  </div>

  <!-- KPI Grid -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">执行用例总数</div>
      <div class="kpi-value">{total_tests}</div>
    </div>
    <div class="kpi-card pass">
      <div class="kpi-title">通过用例 (PASS)</div>
      <div class="kpi-value">{total_passed}</div>
    </div>
    <div class="kpi-card fail">
      <div class="kpi-title">失败用例 (FAIL)</div>
      <div class="kpi-value">{total_failed}</div>
    </div>
    <div class="kpi-card dur">
      <div class="kpi-title">累计耗时</div>
      <div class="kpi-value">{total_duration:.1f}s</div>
    </div>
    <div class="donut-card">
      <svg class="donut-svg" viewBox="0 0 100 100">
        <circle class="donut-circle-bg" cx="50" cy="50" r="45"></circle>
        <circle class="donut-circle-val" cx="50" cy="50" r="45"></circle>
      </svg>
      <div class="donut-label">{pass_rate:.1f}%</div>
      <div class="donut-desc">总体通过率</div>
    </div>
  </div>

  <!-- Suite Breakdown Table -->
  <div class="section-title">📦 测试套件维度汇总</div>
  <div class="suite-table-wrap">
    <table>
      <thead>
        <tr>
          <th>测试套件 (Suite)</th>
          <th>用例数</th>
          <th>通过</th>
          <th>失败</th>
          <th>通过率</th>
          <th>耗时</th>
        </tr>
      </thead>
      <tbody>
"""

    for s in suite_summaries:
        fill_class = "has-fail" if s["failed"] > 0 else ""
        html_content += f"""        <tr>
          <td><strong>{html.escape(s["name"])}</strong></td>
          <td>{s["total"]}</td>
          <td style="color: var(--status-pass); font-weight: 600;">{s["passed"]}</td>
          <td style="color: {'var(--status-fail)' if s['failed'] > 0 else 'inherit'}; font-weight: 600;">{s["failed"]}</td>
          <td>
            <div style="display: flex; align-items: center; gap: 8px;">
              <div class="progress-bar-bg"><div class="progress-bar-fill {fill_class}" style="width: {s['rate']:.1f}%;"></div></div>
              <span>{s['rate']:.1f}%</span>
            </div>
          </td>
          <td style="font-family: var(--font-mono); color: var(--text-muted);">{s["duration"]:.2f}s</td>
        </tr>
"""

    html_content += f"""      </tbody>
    </table>
  </div>

  <!-- Controls Filter -->
  <div class="section-title">🔍 用例执行明细与诊断</div>
  <div class="controls">
    <div class="filter-btn-group">
      <button class="filter-btn active" onclick="setFilter('ALL')">全部 ({total_tests})</button>
      <button class="filter-btn" onclick="setFilter('PASS')">通过 ({total_passed})</button>
      <button class="filter-btn" onclick="setFilter('FAIL')">失败 ({total_failed})</button>
    </div>
    <select class="suite-select" id="suiteSelect" onchange="applyFilters()">
      <option value="">全部套件 (All Suites)</option>
"""

    for s in suite_summaries:
        html_content += f'      <option value="{html.escape(s["name"])}">{html.escape(s["name"])} ({s["total"]})</option>\n'

    html_content += f"""    </select>
    <input type="text" class="search-input" id="searchInput" placeholder="按用例名称或错误信息关键词实时过滤..." oninput="applyFilters()">
    <button class="filter-btn" onclick="toggleAllCases()">展开/收起全部</button>
  </div>

  <!-- Cases List -->
  <div class="cases-list" id="casesContainer">
    <!-- Populated by JavaScript -->
  </div>
</div>

<script>
  const rawResults = {results_json};
  let currentStatusFilter = 'ALL';
  let allExpanded = false;

  function setFilter(status) {{
    currentStatusFilter = status;
    document.querySelectorAll('.filter-btn-group .filter-btn').forEach(btn => {{
      btn.classList.toggle('active', btn.textContent.startsWith(status === 'ALL' ? '全部' : (status === 'PASS' ? '通过' : '失败')));
    }});
    applyFilters();
  }}

  function applyFilters() {{
    const suite = document.getElementById('suiteSelect').value;
    const query = document.getElementById('searchInput').value.toLowerCase().trim();

    const filtered = rawResults.filter(item => {{
      if (currentStatusFilter !== 'ALL' && item.status !== currentStatusFilter) return false;
      if (suite && item.suite !== suite) return false;
      if (query) {{
        const inTarget = (item.target || '').toLowerCase().includes(query);
        const inMsg = (item.message || '').toLowerCase().includes(query);
        const inDetails = (item.details || '').toLowerCase().includes(query);
        if (!inTarget && !inMsg && !inDetails) return false;
      }}
      return true;
    }});

    renderCases(filtered);
  }}

  function escapeHtml(str) {{
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }}

  function renderCases(items) {{
    const container = document.getElementById('casesContainer');
    if (!items || items.length === 0) {{
      container.innerHTML = '<div class="empty-state">未找到匹配的用例记录</div>';
      return;
    }}

    container.innerHTML = items.map((item, idx) => {{
      const isPass = item.status === 'PASS';
      const badgeClass = isPass ? 'badge-pass' : (item.status === 'FAIL' ? 'badge-fail' : 'badge-skip');
      const hasDetails = Boolean(item.details || item.system_out || item.message);

      return `
        <div class="case-card" id="case_${{idx}}">
          <div class="case-header" onclick="toggleCase(${{idx}})">
            <div class="case-left">
              <span class="badge ${{badgeClass}}">${{escapeHtml(item.status)}}</span>
              <span class="case-target">${{escapeHtml(item.target)}}</span>
              ${{item.message ? `<span class="case-msg">⚠️ ${{escapeHtml(item.message)}}</span>` : ''}}
            </div>
            <div style="display: flex; align-items: center;">
              <span class="case-dur">${{parseFloat(item.duration || 0).toFixed(2)}}s</span>
              <span class="toggle-icon">▼</span>
            </div>
          </div>
          <div class="case-details">
            ${{escapeHtml(item.details || item.system_out || '暂无详细输出')}}
          </div>
        </div>
      `;
    }}).join('');
  }}

  function toggleCase(idx) {{
    const el = document.getElementById(`case_${{idx}}`);
    if (el) el.classList.toggle('open');
  }}

  function toggleAllCases() {{
    allExpanded = !allExpanded;
    document.querySelectorAll('.case-card').forEach(card => {{
      card.classList.toggle('open', allExpanded);
    }});
  }}

  // Initial render
  applyFilters();
</script>
</body>
</html>
"""

    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_content, encoding="utf-8")

    return html_content


def export_html_from_runs(runs_dir: Path, output_file: Optional[Path] = None, title: str = "fbasecman 回归测试执行报告") -> str:
    """Convenience helper to read all runs and generate a self-contained HTML report."""
    results = collect_results_from_runs(runs_dir)
    return generate_html_report(results, output_file=output_file, title=title)
