/**
 * fbasecman Regression Web Dashboard Frontend Logic
 */

(function () {
  'use strict';

  // Global State
  let suitesData = [];
  let currentFilter = 'all'; // all, pass, fail, untested
  let searchQuery = '';
  let expandedSuites = new Set(['high_availability']); // default expand high_availability
  let activeTaskId = null;
  let isTaskRunning = false;
  let taskPollTimer = null;
  let terminalLogOffset = 0;
  let terminalAutoScroll = true;
  let currentModalTarget = null;
  let activeModalTab = 'tab-steps';

  // DOM Elements
  const treeContainer = document.getElementById('treeContainer');
  const searchInput = document.getElementById('searchInput');
  const btnClearSearch = document.getElementById('btnClearSearch');
  const filterPills = document.querySelectorAll('.filter-pill');
  const btnExpandAll = document.getElementById('btnExpandAll');
  const btnCollapseAll = document.getElementById('btnCollapseAll');
  const btnRefreshAll = document.getElementById('btnRefreshAll');
  const btnRunFailed = document.getElementById('btnRunFailed');
  const btnHelpDoc = document.getElementById('btnHelpDoc');
  const helpModalOverlay = document.getElementById('helpModalOverlay');
  const btnHelpClose = document.getElementById('btnHelpClose');

  // Filter Banner Elements
  const filterBanner = document.getElementById('filterBanner');
  const filterBannerText = document.getElementById('filterBannerText');
  const btnOpenFailedModal = document.getElementById('btnOpenFailedModal');
  const btnBannerRunFailed = document.getElementById('btnBannerRunFailed');
  const btnClearFilter = document.getElementById('btnClearFilter');

  // Failed Cases Modal Elements
  const failedCasesModalOverlay = document.getElementById('failedCasesModalOverlay');
  const btnFailedModalClose = document.getElementById('btnFailedModalClose');
  const modalFailedCountBadge = document.getElementById('modalFailedCountBadge');
  const btnModalRunAllFailed = document.getElementById('btnModalRunAllFailed');
  const failedCasesList = document.getElementById('failedCasesList');

  // Terminal Elements
  const terminalDrawer = document.getElementById('terminalDrawer');
  const terminalHeader = document.getElementById('terminalHeader');
  const btnToggleTerminal = document.getElementById('btnToggleTerminal');
  const terminalPulse = document.getElementById('terminalPulse');
  const terminalTitle = document.getElementById('terminalTitle');
  const terminalTargetBadge = document.getElementById('terminalTargetBadge');
  const terminalElapsed = document.getElementById('terminalElapsed');
  const btnStopTask = document.getElementById('btnStopTask');
  const btnClearTerminal = document.getElementById('btnClearTerminal');
  const btnCopyTerminal = document.getElementById('btnCopyTerminal');
  const terminalOutput = document.getElementById('terminalOutput');
  const terminalBody = document.getElementById('terminalBody');

  // Report Modal Elements
  const reportModalOverlay = document.getElementById('reportModalOverlay');
  const btnModalClose = document.getElementById('btnModalClose');
  const modalCoreId = document.getElementById('modalCoreId');
  const modalTitle = document.getElementById('modalTitle');
  const modalStatusBadge = document.getElementById('modalStatusBadge');
  const modalSubtitle = document.getElementById('modalSubtitle');
  const modalDuration = document.getElementById('modalDuration');
  const modalDurationTag = document.getElementById('modalDurationTag');
  const btnToggleDetails = document.getElementById('btnToggleDetails');
  const modalDetailsDrawer = document.getElementById('modalDetailsDrawer');
  const btnToggleFullscreen = document.getElementById('btnToggleFullscreen');
  const modalStartTime = document.getElementById('modalStartTime');
  const modalEndTime = document.getElementById('modalEndTime');
  const modalStepsList = document.getElementById('modalStepsList');
  const modalAssertionsList = document.getElementById('modalAssertionsList');
  const modalPurpose = document.getElementById('modalPurpose');
  const modalContentsList = document.getElementById('modalContentsList');
  const modalReason = document.getElementById('modalReason');
  const modalKeyConfig = document.getElementById('modalKeyConfig');
  const modalRawReport = document.getElementById('modalRawReport');
  const btnCopyRawReport = document.getElementById('btnCopyRawReport');
  const logFileSelect = document.getElementById('logFileSelect');
  const btnRefreshLog = document.getElementById('btnRefreshLog');
  const modalLogViewer = document.getElementById('modalLogViewer');
  const modalTabs = document.querySelectorAll('.modal-tabs .tab-btn');
  const tabTopologyBtn = document.getElementById('tabTopologyBtn');
  const modalTopologyContainer = document.getElementById('modalTopologyContainer');
  const tabBacktraceBtn = document.getElementById('tabBacktraceBtn');
  const modalBacktraceContent = document.getElementById('modalBacktraceContent');

  // Metrics Elements
  const metricTotalCases = document.getElementById('metricTotalCases');
  const metricPass = document.getElementById('metricPass');
  const metricFail = document.getElementById('metricFail');
  const metricUntested = document.getElementById('metricUntested');
  const metricPassRate = document.getElementById('metricPassRate');
  const metricPassRatio = document.getElementById('metricPassRatio');
  const metricSuiteCount = document.getElementById('metricSuiteCount');
  const passProgressBar = document.getElementById('passProgressBar');
  const systemStatus = document.getElementById('systemStatus');
  const statusText = document.getElementById('statusText');

  // =========================================================================
  // Cyberpunk Toast Notification System
  // =========================================================================
  function showToast(message, type = 'info', duration = 2800) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const item = document.createElement('div');
    item.className = `toast-item ${type}`;
    const iconMap = {
      success: '✅',
      warning: '⚠️',
      error: '❌',
      info: 'ℹ️',
    };
    item.innerHTML = `
      <span class="toast-icon">${iconMap[type] || 'ℹ️'}</span>
      <span class="toast-msg">${escapeHtml(message)}</span>
    `;
    container.appendChild(item);

    setTimeout(() => {
      item.classList.add('toast-fadeout');
      setTimeout(() => {
        if (item.parentNode) item.parentNode.removeChild(item);
      }, 260);
    }, duration);
  }

  // =========================================================================
  // Initialization & Data Loading
  // =========================================================================
  function init() {
    initModeSwitcher();
    setupDeployActions();
    setupStableActions();
    setupCanvasZoomAndPan();
    setupNodeInspector();
    setupSqlWorkbench();
    setupEventListeners();
    fetchEnvStatusAndRenderTopo();
    loadDashboardData();
    checkTaskStatus(); // Check if a background task was already running
  }

  async function loadDashboardData() {
    try {
      const [suitesResp, statsResp] = await Promise.all([
        fetch('/api/suites').then((r) => r.json()),
        fetch('/api/stats').then((r) => r.json()),
      ]);

      if (suitesResp.status === 'ok') {
        suitesData = suitesResp.suites;
        renderTree();
        updateFilterCounts();
      }

      if (statsResp.status === 'ok') {
        updateMetrics(statsResp);
      }
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
      treeContainer.innerHTML = `
        <div class="loading-state">
          <span style="color: var(--color-fail); font-size: 1.5rem;">⚠️</span>
          <span>加载测试数据失败，请检查 Web 服务状态。</span>
          <button class="nav-btn" onclick="location.reload()">重新加载</button>
        </div>
      `;
    }
  }

  function updateMetrics(stats) {
    metricTotalCases.textContent = stats.total_cases;
    metricPass.textContent = stats.passed;
    metricFail.textContent = stats.failed;
    metricUntested.textContent = stats.untested;
    metricPassRate.textContent = `${stats.pass_rate}%`;
    metricPassRatio.textContent = `${stats.passed} / ${stats.total_cases}`;
    metricSuiteCount.textContent = `${stats.total_suites} 个测试组`;
    passProgressBar.style.width = `${stats.pass_rate}%`;

    const indicator = systemStatus.querySelector('.status-indicator-dot');
    if (stats.is_running) {
      indicator.className = 'status-indicator-dot running';
      statusText.textContent = `执行中: ${stats.current_target || ''}`;
    } else {
      indicator.className = 'status-indicator-dot idle';
      statusText.textContent = '系统就绪';
    }
  }

  function updateFilterCounts() {
    let total = 0, passed = 0, failed = 0, untested = 0;
    suitesData.forEach((s) => {
      total += s.cases.length;
      passed += s.pass_count;
      failed += s.fail_count;
      untested += s.untested_count;
    });

    document.getElementById('pillAllCount').textContent = total;
    document.getElementById('pillPassCount').textContent = passed;
    document.getElementById('pillFailCount').textContent = failed;
    document.getElementById('pillUntestedCount').textContent = untested;
  }

  // =========================================================================
  // Tree Rendering (Suite Accordion + Case Rows)
  // Layout requirement:
  // [Core ID 徽章] 用例名称/中文说明 ──── [执行结果] [耗时] [▶ 执行] [📄 查看报告]
  // =========================================================================
  function renderTree() {
    const q = searchQuery.trim().toLowerCase();

    const filteredSuites = suitesData.map((suite) => {
      const filteredCases = suite.cases.filter((c) => {
        // Status filter
        if (currentFilter === 'pass' && c.status !== 'PASS') return false;
        if (currentFilter === 'fail' && c.status !== 'FAIL') return false;
        if (currentFilter === 'untested' && c.status !== 'UNTESTED') return false;

        // Search filter
        if (q) {
          const matchName = c.name.toLowerCase().includes(q);
          const matchTarget = c.target.toLowerCase().includes(q);
          const matchCore = (c.core_id || '').toLowerCase().includes(q);
          const matchSum = (c.summary || '').toLowerCase().includes(q);
          return matchName || matchTarget || matchCore || matchSum;
        }
        return true;
      });

      return {
        ...suite,
        cases: filteredCases,
      };
    });

    const hasAnyCases = filteredSuites.some((s) => s.cases.length > 0);
    if (!hasAnyCases) {
      treeContainer.innerHTML = `
        <div class="loading-state">
          <span>🔍 没有符合当前筛选条件的测试用例</span>
        </div>
      `;
      return;
    }

    treeContainer.innerHTML = filteredSuites
      .filter((s) => s.cases.length > 0)
      .map((suite) => {
        const isExpanded = expandedSuites.has(suite.id) || q.length > 0;
        const total = suite.cases.length;
        const passCount = suite.cases.filter((c) => c.status === 'PASS').length;
        const failCount = suite.cases.filter((c) => c.status === 'FAIL').length;
        const untestedCount = suite.cases.filter((c) => c.status === 'UNTESTED').length;

        return `
          <div class="suite-card ${isExpanded ? 'expanded' : ''}" data-suite-id="${suite.id}">
            <div class="suite-header" onclick="window.dashboard.toggleSuite('${suite.id}')">
              <div class="suite-header-left">
                <span class="suite-chevron">▶</span>
                <div class="suite-title-group">
                  <div class="suite-title-row">
                    <span class="suite-title">${escapeHtml(suite.title)}</span>
                    <span class="suite-id-tag">${escapeHtml(suite.id)}</span>
                  </div>
                  <span class="suite-description">${escapeHtml(suite.description)}</span>
                </div>
              </div>

              <div class="suite-header-right">
                <div class="suite-stats-pill">
                  <span class="suite-stat-item pass">✓ ${passCount}</span>
                  ${failCount > 0 ? `<span class="suite-stat-item fail">✗ ${failCount}</span>` : ''}
                  <span class="suite-stat-item untested">○ ${untestedCount}</span>
                  <span class="suite-stat-item total">共 ${total} 项</span>
                </div>

                <button class="suite-action-btn"
                        onclick="event.stopPropagation(); window.dashboard.runTarget('${suite.id}', '${escapeHtml(suite.title)}')"
                        ${isTaskRunning ? 'disabled' : ''}
                        title="依次执行该分类下的全部用例">
                  <span>▶</span> 执行整组
                </button>

                ${(suite.id === 'high_availability' || suite.id === 'ha_commands') ? `
                  <button class="suite-action-btn topo"
                          onclick="event.stopPropagation(); window.dashboard.openSuiteTopology('${suite.id}')"
                          title="打开该分类下的高可用 3D 全息拓扑看板">
                    <span>🪐</span> 3D 拓扑
                  </button>
                ` : ''}
              </div>
            </div>

            <div class="suite-cases-list">
              ${suite.cases.map((c) => renderCaseRow(c)).join('')}
            </div>
          </div>
        `;
      })
      .join('');
  }

  function renderCaseRow(c) {
    const isRunningThis = isTaskRunning && activeTaskId === c.target;
    const statusClass = (c.status || 'untested').toLowerCase();
    const statusText = c.status || 'UNTESTED';

    const coreBadgeHtml = c.core_id
      ? `<span class="core-badge" title="方案用例编号">${escapeHtml(c.core_id)}</span>`
      : `<span class="core-badge" style="visibility: hidden; min-width: 68px;">-</span>`;

    const statusBadgeHtml = isRunningThis
      ? `<span class="result-badge running"><span class="spinner-sm"></span> RUNNING</span>`
      : `<span class="result-badge ${statusClass}">
          ${c.status === 'PASS' ? '✓' : c.status === 'FAIL' ? '✗' : '○'} ${statusText}
        </span>`;

    const canViewReport = c.has_report || c.status === 'PASS' || c.status === 'FAIL';
    const isHaCase = c.target.startsWith('high_availability') || c.target.startsWith('ha_commands');

    return `
      <div class="case-row ${isRunningThis ? 'running' : ''}" data-target="${escapeHtml(c.target)}" id="case_${escapeHtml(c.id)}">
        <div class="case-left">
          ${coreBadgeHtml}
          <div class="case-name-group">
            <span class="case-name" title="${escapeHtml(c.target)}">${escapeHtml(c.name)}</span>
            <span class="case-summary" title="${escapeHtml(c.summary)}">${escapeHtml(c.summary)}</span>
          </div>
        </div>

        <div class="case-right">
          ${statusBadgeHtml}
          <span class="duration-label" title="测试耗时">${escapeHtml(c.duration || '-')}</span>

          <button class="btn-run-case"
                  onclick="window.dashboard.runTarget('${escapeHtml(c.target)}', '${escapeHtml(c.name)}')"
                  ${isTaskRunning ? 'disabled' : ''}
                  title="单独执行此测试项">
            ${isRunningThis ? '<span class="spinner-sm"></span>' : '▶'} 执行
          </button>

          <button class="btn-view-report"
                  onclick="window.dashboard.viewReport('${escapeHtml(c.target)}')"
                  ${canViewReport ? '' : 'disabled'}
                  title="${canViewReport ? '点击查看沉浸式步骤报告' : '用例尚未执行，暂无报告'}">
            📄 查看报告
          </button>

          ${isHaCase ? `
            <button class="btn-view-topo"
                    onclick="window.dashboard.viewReport('${escapeHtml(c.target)}', 'tab-topology')"
                    ${canViewReport ? '' : 'disabled'}
                    title="直接打开高可用 3D 全息拓扑看板">
              🪐 3D拓扑
            </button>
          ` : ''}
        </div>
      </div>
    `;
  }

  // =========================================================================
  // Task Execution & Terminal Log Streaming
  // =========================================================================
  async function runTarget(target, displayName) {
    if (isTaskRunning) {
      alert(`当前已有测试任务正在执行中 (${activeTaskId})，请等待完成或点击终端右上角的终止按钮。`);
      return;
    }

    try {
      const resp = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: target }),
      });
      const data = await resp.json();

      if (resp.status !== 200 || data.status !== 'ok') {
        alert(data.message || '启动任务失败');
        return;
      }

      // Expand terminal drawer
      expandTerminal();
      terminalOutput.textContent = `[Web Client] 正在启动: ${displayName || target}...\n`;
      terminalLogOffset = 0;
      activeTaskId = target;
      isTaskRunning = true;

      // Update terminal UI
      terminalPulse.className = 'terminal-pulse active';
      terminalTitle.textContent = `正在执行: ${target}`;
      terminalTargetBadge.style.display = 'inline-block';
      terminalTargetBadge.textContent = target;
      btnStopTask.style.display = 'inline-block';

      // Update case row to show running state
      renderTree();

      // Start polling
      startPollingTask();
    } catch (err) {
      alert(`网络请求异常: ${err.message}`);
    }
  }

  async function stopCurrentTask() {
    if (!isTaskRunning) return;
    if (!confirm('确认终止当前正在运行的测试任务吗？')) return;

    btnStopTask.disabled = true;
    btnStopTask.textContent = '正在终止...';
    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch (err) {
      console.error('Stop error:', err);
    }
  }

  function startPollingTask() {
    if (taskPollTimer) clearInterval(taskPollTimer);
    taskPollTimer = setInterval(pollTaskStatus, 800);
  }

  async function pollTaskStatus() {
    try {
      const resp = await fetch(`/api/task?offset=${terminalLogOffset}`);
      const data = await resp.json();
      if (data.status !== 'ok') return;

      const task = data.task;
      terminalElapsed.textContent = `${task.elapsed}s`;

      if (task.lines && task.lines.length > 0) {
        terminalOutput.textContent += task.lines.join('\n') + '\n';
        terminalLogOffset = task.offset;
        if (terminalAutoScroll) {
          terminalBody.scrollTop = terminalBody.scrollHeight;
        }
      }

      if (!task.active && task.status !== 'running') {
        // Task has finished
        clearInterval(taskPollTimer);
        taskPollTimer = null;
        isTaskRunning = false;
        activeTaskId = null;

        terminalPulse.className = 'terminal-pulse';
        btnStopTask.style.display = 'none';
        btnStopTask.disabled = false;
        btnStopTask.textContent = '⏹ 终止执行';

        const isSuccess = task.status === 'success';
        terminalTitle.textContent = isSuccess ? '执行完成 (SUCCESS)' : '执行失败 (FAILED)';

        // Reload data and update UI
        await loadDashboardData();
      }
    } catch (err) {
      console.error('Poll task failed:', err);
    }
  }

  async function checkTaskStatus() {
    try {
      const resp = await fetch('/api/task?offset=0');
      const data = await resp.json();
      if (data.status === 'ok' && data.task.active) {
        // Already running in background
        activeTaskId = data.task.target;
        if (data.task.target.startsWith('env.')) {
          const action = data.task.target.replace('env.', '');
          const meta = (typeof CLUSTER_ACTION_META !== 'undefined' && CLUSTER_ACTION_META[action]) ? CLUSTER_ACTION_META[action] : {
            title: `集群操作: ${action}`,
            icon: '⚡',
            runningText: '执行中...',
            isStepper: action === 'setup',
          };
          isClusterActionRunning = true;
          currentClusterAction = action;
          const btnId = typeof CLUSTER_BUTTON_MAP !== 'undefined' ? CLUSTER_BUTTON_MAP[action] : null;
          lockClusterToolbar(btnId, meta.runningText);

          const deployRunningBar = document.getElementById('deployRunningBar');
          const runningActionText = document.getElementById('runningActionText');
          if (deployRunningBar) deployRunningBar.style.display = 'flex';
          if (runningActionText) runningActionText.textContent = meta.title;

          openEnvActionModal();
          startPollingClusterTask(data.task.target);
        } else {
          isTaskRunning = true;
          terminalPulse.className = 'terminal-pulse active';
          terminalTitle.textContent = `正在执行: ${data.task.target}`;
          terminalTargetBadge.style.display = 'inline-block';
          terminalTargetBadge.textContent = data.task.target;
          btnStopTask.style.display = 'inline-block';
          expandTerminal();
          startPollingTask();
        }
      }
    } catch (err) {
      // Ignore
    }
  }

  function expandTerminal() {
    terminalDrawer.classList.remove('collapsed');
    terminalDrawer.classList.add('expanded');
    btnToggleTerminal.textContent = '▼ 收起日志';
  }

  function collapseTerminal() {
    terminalDrawer.classList.remove('expanded');
    terminalDrawer.classList.add('collapsed');
    btnToggleTerminal.textContent = '▲ 展开日志';
  }

  function toggleTerminal() {
    if (terminalDrawer.classList.contains('collapsed')) {
      expandTerminal();
    } else {
      collapseTerminal();
    }
  }

  function openTerminal() {
    expandTerminal();
  }

  function startTaskPolling(target) {
    terminalLogOffset = 0;
    activeTaskId = target;
    isTaskRunning = true;
    terminalPulse.className = 'terminal-pulse active';
    terminalTitle.textContent = `正在执行: ${target}`;
    terminalTargetBadge.style.display = 'inline-block';
    terminalTargetBadge.textContent = target;
    btnStopTask.style.display = 'inline-block';
    startPollingTask();
  }

  // =========================================================================
  // Immersive Report Modal
  // =========================================================================
  async function viewReport(target, defaultTab = 'tab-steps') {
    currentModalTarget = target;
    reportModalOverlay.style.display = 'flex';
    modalTitle.textContent = `加载中: ${target}...`;
    modalSubtitle.textContent = '正在获取测试报告与执行步骤详情...';
    modalStepsList.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>加载报告数据...</span></div>';
    modalAssertionsList.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';
    modalRawReport.textContent = '加载中...';
    modalLogViewer.textContent = '加载中...';

    // Switch to desired tab
    switchModalTab(defaultTab || 'tab-steps');

    try {
      const resp = await fetch(`/api/report?target=${encodeURIComponent(target)}`);
      const data = await resp.json();
      if (data.status !== 'ok' || !data.report.found) {
        alert(data.report ? data.report.error : '获取报告失败');
        closeModal();
        return;
      }

      renderReportModal(data.report, defaultTab);
    } catch (err) {
      alert(`加载测试报告发生网络错误: ${err.message}`);
      closeModal();
    }
  }

  function renderReportModal(report, defaultTab = 'tab-steps') {
    // Header
    modalTitle.textContent = report.target;
    modalStatusBadge.textContent = report.status || 'PASS';
    modalStatusBadge.className = `modal-status-badge ${report.status === 'PASS' ? 'pass' : 'fail'}`;

    // Find core_id if available
    let coreId = '';
    let summaryText = '';
    for (const s of suitesData) {
      const foundCase = s.cases.find((c) => c.target === report.target);
      if (foundCase) {
        coreId = foundCase.core_id;
        summaryText = foundCase.summary;
        break;
      }
    }

    if (coreId) {
      modalCoreId.style.display = 'inline-block';
      modalCoreId.textContent = coreId;
    } else {
      modalCoreId.style.display = 'none';
    }

    modalSubtitle.textContent = summaryText || report.purpose || report.target;
    modalStartTime.textContent = report.start_time || '-';
    modalEndTime.textContent = report.end_time || '-';

    // Calculate duration
    let durText = '-';
    if (report.start_time && report.end_time) {
      try {
        const d1 = new Date(report.start_time.replace(/-/g, '/'));
        const d2 = new Date(report.end_time.replace(/-/g, '/'));
        const diffSec = Math.abs((d2 - d1) / 1000);
        durText = `${diffSec.toFixed(2)}s`;
      } catch (e) {
        durText = '-';
      }
    }
    if (modalDuration) modalDuration.textContent = durText;
    if (modalDurationTag) {
      modalDurationTag.textContent = durText !== '-' ? `耗时: ${durText}` : '';
      modalDurationTag.style.display = durText !== '-' ? 'inline-block' : 'none';
    }

    // Tabs counters
    document.getElementById('tabStepCount').textContent = report.steps ? report.steps.length : 0;
    document.getElementById('tabAssertCount').textContent = report.assertions ? report.assertions.length : 0;

    // Tab 1: Steps Timeline
    if (report.steps && report.steps.length > 0) {
      modalStepsList.innerHTML = report.steps
        .map((st) => {
          const pass = st.status === 'PASS';
          return `
            <div class="step-card ${pass ? 'pass' : 'fail'}">
              <div class="step-card-header">
                <span class="step-title">${escapeHtml(st.title)}</span>
                <span class="step-status ${pass ? 'pass' : 'fail'}">${st.status}</span>
              </div>

              <div class="step-detail-grid">
                ${st.action ? `<span class="detail-label">动作</span><span class="detail-value">${escapeHtml(st.action)}</span>` : ''}
                ${st.command ? `<span class="detail-label">执行命令</span><span class="detail-value command">${escapeHtml(st.command)}</span>` : ''}
                ${st.expected ? `<span class="detail-label">预期结果</span><span class="detail-value">${escapeHtml(st.expected)}</span>` : ''}
                ${st.actual ? `<span class="detail-label">实际结果</span><span class="detail-value">${escapeHtml(st.actual)}</span>` : ''}
                ${st.evidence ? `<span class="detail-label">日志证据</span><span class="detail-value command">${escapeHtml(st.evidence)}</span>` : ''}
              </div>

              ${st.state_table ? `
                <div class="state-table-block" title="中间数据库查询状态">${escapeHtml(st.state_table)}</div>
              ` : ''}
            </div>
          `;
        })
        .join('');
    } else {
      modalStepsList.innerHTML = '<div class="info-block">该用例未定义结构化步骤或报告无独立步骤分块，请参考原始报告。</div>';
    }

    // Tab 2: Assertions
    if (report.assertions && report.assertions.length > 0) {
      modalAssertionsList.innerHTML = report.assertions
        .map((as) => {
          const pass = as.status === 'PASS';
          return `
            <div class="assert-item ${pass ? 'pass' : 'fail'}">
              <span class="assert-icon ${pass ? 'pass' : 'fail'}">${pass ? '✓' : '✗'}</span>
              <div class="assert-content">
                <div class="assert-title">${escapeHtml(as.title)}</div>
                <div class="assert-grid">
                  ${as.expected ? `<span class="detail-label">预期:</span><span class="detail-value">${escapeHtml(as.expected)}</span>` : ''}
                  ${as.actual ? `<span class="detail-label">实际:</span><span class="detail-value">${escapeHtml(as.actual)}</span>` : ''}
                </div>
              </div>
            </div>
          `;
        })
        .join('');
    } else {
      modalAssertionsList.innerHTML = '<div class="info-block">未解析到独立检测项，全部检测均已在各步骤中断言。</div>';
    }

    // Tab 1.5: Topology Visualizer
    if (report.topology && report.topology.has_topology && report.topology.clusters && report.topology.clusters.length > 0) {
      if (tabTopologyBtn) tabTopologyBtn.style.display = 'inline-block';
      renderTopologyView(report.topology);
      if (defaultTab === 'tab-topology') {
        switchModalTab('tab-topology');
      }
    } else {
      if (tabTopologyBtn) tabTopologyBtn.style.display = 'none';
      if (modalTopologyContainer) modalTopologyContainer.innerHTML = '<div class="info-block">未解析到高可用集群拓扑信息。</div>';
    }

    // Tab 1.8: Crash Backtrace
    if (report.backtrace) {
      if (tabBacktraceBtn) tabBacktraceBtn.style.display = 'inline-block';
      if (modalBacktraceContent) modalBacktraceContent.textContent = report.backtrace;
      if (report.status === 'FAIL') {
        switchModalTab('tab-backtrace');
      }
    } else {
      if (tabBacktraceBtn) tabBacktraceBtn.style.display = 'none';
      if (modalBacktraceContent) modalBacktraceContent.textContent = '未发生进程崩溃，无 Core Dump / GDB Backtrace。';
    }

    // Tab 3: Overview & Config
    modalPurpose.textContent = report.purpose || '未提供验证目的描述';
    if (report.test_contents && report.test_contents.length > 0) {
      modalContentsList.innerHTML = report.test_contents
        .map((item) => `<li>${escapeHtml(item)}</li>`)
        .join('');
    } else {
      modalContentsList.innerHTML = '<li>未提供测试内容细项</li>';
    }
    modalReason.textContent = report.reason || '所有检测项符合预期。';
    modalKeyConfig.textContent = report.key_config || '使用默认回归测试环境配置。';

    // Tab 4: Raw Report
    modalRawReport.textContent = report.raw_text || '无报告内容';

    // Tab 5: Runtime Logs Selector
    logFileSelect.innerHTML = '';
    if (report.available_logs && report.available_logs.length > 0) {
      report.available_logs.forEach((lf) => {
        const opt = document.createElement('option');
        opt.value = lf;
        opt.textContent = lf;
        logFileSelect.appendChild(opt);
      });
      loadCaseLog(report.target, report.available_logs[0]);
    } else {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '暂无独立日志文件';
      logFileSelect.appendChild(opt);
      modalLogViewer.textContent = '未发现运行时输出日志文件';
    }
  }

  async function loadCaseLog(target, filename) {
    if (!filename) return;
    modalLogViewer.textContent = `正在加载日志: ${filename}...`;
    try {
      const resp = await fetch(`/api/log?target=${encodeURIComponent(target)}&file=${encodeURIComponent(filename)}`);
      const data = await resp.json();
      if (data.status === 'ok') {
        modalLogViewer.textContent = data.content || '(日志文件为空)';
      } else {
        modalLogViewer.textContent = `加载日志失败: ${data.message}`;
      }
    } catch (err) {
      modalLogViewer.textContent = `请求失败: ${err.message}`;
    }
  }

  function switchModalTab(tabId) {
    activeModalTab = tabId;
    const topoToggleBar = document.getElementById('topoViewToggleBar');

    if (tabId === 'tab-topology') {
      if (topoToggleBar) topoToggleBar.style.display = 'flex';
      onTopologyTabActivated();
    } else {
      if (topoToggleBar) topoToggleBar.style.display = 'none';
    }

    modalTabs.forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
    });
    document.querySelectorAll('.modal-body .tab-pane').forEach((pane) => {
      pane.classList.toggle('active', pane.id === tabId);
    });
  }

  function closeModal() {
    reportModalOverlay.style.display = 'none';
    currentModalTarget = null;
    const modalContainer = document.querySelector('.modal-container');
    if (modalContainer) {
      modalContainer.classList.remove('fullscreen-mode');
    }
    if (btnToggleFullscreen) {
      btnToggleFullscreen.textContent = '⛶';
      btnToggleFullscreen.title = '全屏/窗口切换 (F11)';
    }
    if (modalDetailsDrawer) {
      modalDetailsDrawer.style.display = 'none';
    }
    if (btnToggleDetails) {
      btnToggleDetails.classList.remove('active');
    }
    cleanUp3DScene();
    cleanUpTopologyAnimation();
  }

  // =========================================================================
  // Event Listeners & UI Controls
  // =========================================================================
  function setupEventListeners() {
    // Search input with debounce
    let searchTimer = null;
    searchInput.addEventListener('input', (e) => {
      searchQuery = e.target.value;
      btnClearSearch.style.display = searchQuery ? 'block' : 'none';
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        renderTree();
      }, 200);
    });

    btnClearSearch.addEventListener('click', () => {
      searchInput.value = '';
      searchQuery = '';
      btnClearSearch.style.display = 'none';
      renderTree();
    });

    // Filter pills
    filterPills.forEach((pill) => {
      pill.addEventListener('click', () => {
        applyFilter(pill.getAttribute('data-filter'), false);
      });
    });

    // Clickable Metric Cards (Click "已失败" to view failed cases!)
    document.querySelectorAll('.metric-card.clickable').forEach((card) => {
      card.addEventListener('click', () => {
        const filterType = card.getAttribute('data-filter');
        if (filterType) {
          applyFilter(filterType, true);
        }
      });
    });

    // Dedicated action tag on "已失败 (FAIL)" card to open modal directly
    const failActionTag = document.querySelector('#cardFailedCases .metric-action-tag');
    if (failActionTag) {
      failActionTag.addEventListener('click', (e) => {
        e.stopPropagation();
        openFailedCasesModal();
      });
    }

    // Filter Banner Buttons
    if (btnOpenFailedModal) {
      btnOpenFailedModal.addEventListener('click', openFailedCasesModal);
    }
    if (btnBannerRunFailed) {
      btnBannerRunFailed.addEventListener('click', () => {
        runTarget('failed', '重新执行所有失败用例');
      });
    }
    if (btnClearFilter) {
      btnClearFilter.addEventListener('click', () => {
        applyFilter('all', false);
      });
    }

    // Top Nav 3D Topology Button
    const btnTopNavTopo = document.getElementById('btnTopNavTopo');
    if (btnTopNavTopo) {
      btnTopNavTopo.addEventListener('click', () => {
        openGlobalTopologyModal();
      });
    }

    // Failed Cases Modal Controls
    if (btnFailedModalClose) {
      btnFailedModalClose.addEventListener('click', closeFailedModal);
    }
    if (failedCasesModalOverlay) {
      failedCasesModalOverlay.addEventListener('click', (e) => {
        if (e.target === failedCasesModalOverlay) closeFailedModal();
      });
    }
    if (btnModalRunAllFailed) {
      btnModalRunAllFailed.addEventListener('click', () => {
        closeFailedModal();
        runTarget('failed', '批量重跑所有失败用例');
      });
    }

    // Expand / Collapse all
    btnExpandAll.addEventListener('click', () => {
      suitesData.forEach((s) => expandedSuites.add(s.id));
      renderTree();
    });

    btnCollapseAll.addEventListener('click', () => {
      expandedSuites.clear();
      renderTree();
    });

    if (btnRefreshAll) {
      btnRefreshAll.addEventListener('click', async () => {
        btnRefreshAll.classList.add('loading');
        await loadDashboardData();
        btnRefreshAll.classList.remove('loading');
      });
    }

    // Rerun failed
    if (btnRunFailed) {
      btnRunFailed.addEventListener('click', () => {
        if (confirm('确认重新执行所有上次失败的测试用例吗？')) {
          runTarget('failed', '所有失败项重跑');
        }
      });
    }

    // Sidebar toggle controls
    const toggleSidebar = () => {
      const isCollapsed = document.body.classList.toggle('sidebar-collapsed');
      localStorage.setItem('fbasecman_sidebar_collapsed', isCollapsed ? '1' : '0');
    };

    if (localStorage.getItem('fbasecman_sidebar_collapsed') === '1') {
      document.body.classList.add('sidebar-collapsed');
    }

    const btnToggleSidebar = document.getElementById('btnToggleSidebar');
    const btnCollapseSidebarTop = document.getElementById('btnCollapseSidebarTop');
    if (btnToggleSidebar) btnToggleSidebar.addEventListener('click', toggleSidebar);
    if (btnCollapseSidebarTop) btnCollapseSidebarTop.addEventListener('click', toggleSidebar);

    // Terminal controls
    terminalHeader.addEventListener('click', (e) => {
      if (e.target.closest('.terminal-controls')) return;
      toggleTerminal();
    });

    btnToggleTerminal.addEventListener('click', toggleTerminal);
    btnStopTask.addEventListener('click', stopCurrentTask);

    btnClearTerminal.addEventListener('click', () => {
      terminalOutput.textContent = '';
    });

    btnCopyTerminal.addEventListener('click', () => {
      copyToClipboard(terminalOutput.textContent, '终端日志已复制到剪贴板！');
    });

    // Modal controls
    btnModalClose.addEventListener('click', closeModal);
    if (btnToggleDetails && modalDetailsDrawer) {
      btnToggleDetails.addEventListener('click', () => {
        const isHidden = modalDetailsDrawer.style.display === 'none';
        modalDetailsDrawer.style.display = isHidden ? 'block' : 'none';
        btnToggleDetails.classList.toggle('active', isHidden);
      });
    }
    if (btnToggleFullscreen) {
      btnToggleFullscreen.addEventListener('click', () => {
        const modalContainer = document.querySelector('.modal-container');
        if (modalContainer) {
          const isFullscreen = modalContainer.classList.toggle('fullscreen-mode');
          btnToggleFullscreen.textContent = isFullscreen ? '🗗' : '⛶';
          btnToggleFullscreen.title = isFullscreen ? '恢复窗口 (F11)' : '全屏沉浸模式 (F11)';
        }
      });
    }
    reportModalOverlay.addEventListener('click', (e) => {
      if (e.target === reportModalOverlay) closeModal();
    });

    modalTabs.forEach((btn) => {
      btn.addEventListener('click', () => {
        switchModalTab(btn.getAttribute('data-tab'));
      });
    });

    btnCopyRawReport.addEventListener('click', () => {
      copyToClipboard(modalRawReport.textContent, '原始报告已复制到剪贴板！');
    });

    logFileSelect.addEventListener('change', (e) => {
      if (currentModalTarget && e.target.value) {
        loadCaseLog(currentModalTarget, e.target.value);
      }
    });

    btnRefreshLog.addEventListener('click', () => {
      if (currentModalTarget && logFileSelect.value) {
        loadCaseLog(currentModalTarget, logFileSelect.value);
      }
    });

    // Help modal
    btnHelpDoc.addEventListener('click', () => {
      helpModalOverlay.style.display = 'flex';
    });
    btnHelpClose.addEventListener('click', () => {
      helpModalOverlay.style.display = 'none';
    });
    helpModalOverlay.addEventListener('click', (e) => {
      if (e.target === helpModalOverlay) helpModalOverlay.style.display = 'none';
    });

    // Keyboard shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (reportModalOverlay.style.display === 'flex') closeModal();
        if (failedCasesModalOverlay && failedCasesModalOverlay.style.display === 'flex') closeFailedModal();
        if (helpModalOverlay.style.display === 'flex') helpModalOverlay.style.display = 'none';
      }
    });
  }

  // =========================================================================
  // Filter & Failed Cases Modal Handlers
  // =========================================================================
  function applyFilter(filterName, shouldScroll = true) {
    currentFilter = filterName;

    // Update filter pills active state
    filterPills.forEach((p) => {
      p.classList.toggle('active', p.getAttribute('data-filter') === filterName);
    });

    // Update clickable metric card active state
    document.querySelectorAll('.metric-card.clickable').forEach((card) => {
      const match = card.getAttribute('data-filter') === filterName && filterName !== 'all';
      card.classList.toggle('active-filter', match);
    });

    let failedCount = 0;
    suitesData.forEach((s) => {
      failedCount += s.fail_count;
    });

    if (filterName === 'fail') {
      // Auto-expand all suites containing failed cases so user sees them immediately!
      suitesData.forEach((s) => {
        const hasFail = s.cases.some((c) => c.status === 'FAIL');
        if (hasFail) {
          expandedSuites.add(s.id);
        }
      });

      if (filterBanner) {
        filterBanner.style.display = 'flex';
        filterBannerText.textContent = `当前已筛选出 ${failedCount} 项失败用例（分类已自动展开）`;
      }
    } else if (filterName === 'pass') {
      if (filterBanner) {
        filterBanner.style.display = 'flex';
        filterBannerText.textContent = `当前正在查看已通过用例`;
      }
    } else if (filterName === 'untested') {
      if (filterBanner) {
        filterBanner.style.display = 'flex';
        filterBannerText.textContent = `当前正在查看未执行用例`;
      }
    } else {
      if (filterBanner) {
        filterBanner.style.display = 'none';
      }
    }

    renderTree();

    if (shouldScroll) {
      const targetElem = filterBanner && filterBanner.style.display !== 'none' ? filterBanner : treeContainer;
      targetElem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function openFailedCasesModal() {
    if (!failedCasesModalOverlay) return;

    const failedList = [];
    suitesData.forEach((suite) => {
      suite.cases.forEach((c) => {
        if (c.status === 'FAIL') {
          failedList.push({ ...c, suiteTitle: suite.title });
        }
      });
    });

    modalFailedCountBadge.textContent = `${failedList.length} 项失败`;
    failedCasesModalOverlay.style.display = 'flex';

    if (failedList.length === 0) {
      failedCasesList.innerHTML = `
        <div class="loading-state">
          <span style="font-size: 2.2rem;">🎉</span>
          <span style="color: var(--color-pass); font-weight: 600; font-size: 1.1rem;">
            太棒了！当前没有任何失败的测试用例。
          </span>
          <span style="color: var(--text-muted); font-size: 0.85rem;">
            所有已执行的用例均已通过验证。
          </span>
        </div>
      `;
      return;
    }

    failedCasesList.innerHTML = failedList
      .map((c) => `
        <div class="failed-case-card">
          <div class="failed-card-top">
            <div class="failed-card-info">
              ${c.core_id ? `<span class="core-badge">${escapeHtml(c.core_id)}</span>` : ''}
              <span class="failed-card-title">${escapeHtml(c.target)}</span>
              <span class="failed-card-suite">${escapeHtml(c.suite)}</span>
            </div>
            <span class="result-badge fail">✗ FAIL</span>
          </div>

          <div class="case-summary" style="font-size: 0.85rem; color: var(--text-primary);">
            ${escapeHtml(c.summary)}
          </div>

          <div class="failed-card-reason-box" id="reason-${escapeHtml(c.target.replace(/\./g, '_'))}">
            正在载入失败原因与检测项...
          </div>

          <div class="failed-card-bottom">
            <span>测试耗时: ${escapeHtml(c.duration || '-')} &nbsp;|&nbsp; 开始时间: ${escapeHtml(c.timestamp || '-')}</span>
            <div class="failed-card-actions">
              <button class="tool-btn small" onclick="closeFailedModal(); window.dashboard.locateCase('${escapeHtml(c.suite)}', '${escapeHtml(c.id)}')">
                📍 定位用例
              </button>
              <button class="btn-run-case" onclick="closeFailedModal(); window.dashboard.runTarget('${escapeHtml(c.target)}', '${escapeHtml(c.name)}')">
                ▶ 单独重跑
              </button>
              <button class="btn-view-report" onclick="closeFailedModal(); window.dashboard.viewReport('${escapeHtml(c.target)}')">
                📄 查看完整报告
              </button>
            </div>
          </div>
        </div>
      `)
      .join('');

    // Asynchronously fetch detailed failure reasons for each failed item
    failedList.forEach(async (c) => {
      const boxId = `reason-${c.target.replace(/\./g, '_')}`;
      try {
        const resp = await fetch(`/api/report?target=${encodeURIComponent(c.target)}`);
        const data = await resp.json();
        const box = document.getElementById(boxId);
        if (box) {
          if (data.status === 'ok' && data.report) {
            let reasonText = data.report.reason;
            if (!reasonText && data.report.steps) {
              const failedStep = data.report.steps.find((s) => s.status === 'FAIL');
              if (failedStep) {
                reasonText = failedStep.actual || failedStep.expected || '步骤执行失败';
              }
            }
            if (!reasonText && data.report.assertions) {
              const failedAssert = data.report.assertions.find((a) => a.status === 'FAIL');
              if (failedAssert) {
                reasonText = failedAssert.actual || failedAssert.expected || '断言失败';
              }
            }
            box.textContent = `失败原因: ${reasonText || '未捕获到显式失败原因，请点击查看报告'}`;
          } else {
            box.textContent = '暂无详细报告原因';
          }
        }
      } catch (err) {
        const box = document.getElementById(boxId);
        if (box) box.textContent = '获取失败原因超时';
      }
    });
  }

  function closeFailedModal() {
    if (failedCasesModalOverlay) {
      failedCasesModalOverlay.style.display = 'none';
    }
  }

  function openGlobalTopologyModal() {
    let target = 'high_availability.core_13_monitor_confirm';
    for (const s of suitesData) {
      if (s.id === 'high_availability' || s.id === 'ha_commands') {
        const found = s.cases.find((c) => c.status === 'PASS' || c.status === 'FAIL' || c.has_report);
        if (found) {
          target = found.target;
          break;
        }
      }
    }
    viewReport(target, 'tab-topology');
  }

  function openSuiteTopology(suiteId) {
    const s = suitesData.find((x) => x.id === suiteId);
    let target = 'high_availability.core_13_monitor_confirm';
    if (s) {
      const found = s.cases.find((c) => c.status === 'PASS' || c.status === 'FAIL' || c.has_report);
      if (found) target = found.target;
    }
    viewReport(target, 'tab-topology');
  }

  function toggleSuite(suiteId) {
    if (expandedSuites.has(suiteId)) {
      expandedSuites.delete(suiteId);
    } else {
      expandedSuites.add(suiteId);
    }
    renderTree();
  }

  function copyToClipboard(text, successMsg) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        alert(successMsg || '已复制到剪贴板');
      });
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      alert(successMsg || '已复制到剪贴板');
    }
  }


  // =========================================================================
  // High Availability Graphical & 3D Topology Visualizer
  // =========================================================================
  let currentTopologyView = '3d'; // '3d', 'graph', 'cards'
  let cachedTopologyData = null;
  let active3dContext = null;

  function renderTopologyView(topology) {
    cachedTopologyData = topology;
    const btn3d = document.getElementById('btnTopoView3D');
    const btnGraph = document.getElementById('btnTopoViewGraph');
    const btnCards = document.getElementById('btnTopoViewCards');

    if (btn3d && !btn3d._bound) {
      btn3d._bound = true;
      btn3d.addEventListener('click', () => switchTopologyViewMode('3d'));
    }
    if (btnGraph && !btnGraph._bound) {
      btnGraph._bound = true;
      btnGraph.addEventListener('click', () => switchTopologyViewMode('graph'));
    }
    if (btnCards && !btnCards._bound) {
      btnCards._bound = true;
      btnCards.addEventListener('click', () => switchTopologyViewMode('cards'));
    }

    renderCurrentTopology();
  }

  function switchTopologyViewMode(mode) {
    cleanUpTopologyAnimation();
    currentTopologyView = mode;
    const tabTopology = document.getElementById('tab-topology');
    if (tabTopology) {
      tabTopology.classList.toggle('view-graph-mode', mode === 'graph');
    }
    const btn3d = document.getElementById('btnTopoView3D');
    const btnGraph = document.getElementById('btnTopoViewGraph');
    const btnCards = document.getElementById('btnTopoViewCards');

    if (btn3d) btn3d.classList.toggle('active', mode === '3d');
    if (btnGraph) btnGraph.classList.toggle('active', mode === 'graph');
    if (btnCards) btnCards.classList.toggle('active', mode === 'cards');

    renderCurrentTopology();
  }

  function renderCurrentTopology() {
    if (!cachedTopologyData || !modalTopologyContainer) return;
    const tabTopology = document.getElementById('tab-topology');
    if (tabTopology) {
      tabTopology.classList.toggle('view-graph-mode', currentTopologyView === 'graph');
    }

    if (currentTopologyView === '3d' && window.THREE) {
      render3DTopology(cachedTopologyData);
    } else if (currentTopologyView === 'graph' || !window.THREE) {
      cleanUp3DScene();
      modalTopologyContainer.innerHTML = renderTopologyGraphHtml(cachedTopologyData);
      bindTopologyGraphEvents(cachedTopologyData);
    } else {
      cleanUp3DScene();
      modalTopologyContainer.innerHTML = renderTopologyCardsHtml(cachedTopologyData);
    }
  }

  function onTopologyTabActivated() {
    if (currentTopologyView === '3d' && cachedTopologyData) {
      if (!active3dContext) {
        render3DTopology(cachedTopologyData);
      } else if (active3dContext.onResize) {
        setTimeout(() => active3dContext.onResize(), 100);
      }
    }
  }

  function cleanUp3DScene() {
    if (active3dContext) {
      if (active3dContext.animId) {
        cancelAnimationFrame(active3dContext.animId);
      }
      if (active3dContext.resizeObserver) {
        active3dContext.resizeObserver.disconnect();
      }
      if (active3dContext.renderer) {
        try {
          active3dContext.renderer.dispose();
        } catch (e) {}
      }
      active3dContext = null;
    }
  }

  // -------------------------------------------------------------------------
  // 3D WebGL Three.js Holographic Engine
  // -------------------------------------------------------------------------
  function render3DTopology(topology) {
    cleanUp3DScene();

    // Setup DOM containers
    modalTopologyContainer.innerHTML = `
      <div class="topology-3d-wrapper">
        <div class="topology-3d-toolbar">
          <button class="tool-btn" id="btn3dReset" title="重置摄像机默认视角">🔄 重置视角</button>
          <button class="tool-btn" id="btn3dTop" title="切换到俯视全景视角">📐 俯视视角</button>
          <button class="tool-btn active" id="btn3dRotate" title="自动旋转镜头巡航">🔄 自动巡航</button>
        </div>
        <div class="topology-3d-canvas-container" id="topo3dCanvas"></div>
        <div class="topology-3d-hint">🖱️ 左键拖拽旋转 3D 拓扑 · 右键平移 · 滚轮缩放 · 点击节点查看实时详情</div>
      </div>
      <div class="topology-inspector" id="topoInspector">
        <!-- Filled on node select -->
      </div>
      <div class="topology-legend">
        <div class="legend-item"><span class="legend-line write"></span> 绿光粒子: 客户端实时写流 (Primary)</div>
        <div class="legend-item"><span class="legend-line read"></span> 蓝光粒子: 客户端只读负载分流 (Standby)</div>
        <div class="legend-item"><span class="legend-line mmr"></span> 紫色等离子: 多中心 MMR 双向对等复制</div>
        <div class="legend-item"><span class="legend-line rep"></span> 浅蓝脉冲: 集群内流复制 (WAL Stream)</div>
        <div class="legend-item"><span class="legend-line parted"></span> 红色警报: 节点故障离线 / 路由剔除</div>
      </div>
    `;

    const canvasContainer = document.getElementById('topo3dCanvas');
    if (!canvasContainer || !window.THREE) return;

    const width = canvasContainer.clientWidth || 880;
    const height = 520;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x060913, 0.012);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 26, 44);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    canvasContainer.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.maxPolarAngle = Math.PI / 2 + 0.05;
    controls.minDistance = 12;
    controls.maxDistance = 110;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.6;
    controls.target.set(0, 2, 0);

    // Grid Floor
    const grid = new THREE.GridHelper(80, 50, 0x38bdf8, 0x1e293b);
    grid.position.y = -0.05;
    scene.add(grid);

    // Dust particles
    const starGeo = new THREE.BufferGeometry();
    const starCount = 350;
    const starPos = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount * 3; i += 3) {
      starPos[i] = (Math.random() - 0.5) * 110;
      starPos[i + 1] = Math.random() * 35;
      starPos[i + 2] = (Math.random() - 0.5) * 110;
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    const starMat = new THREE.PointsMaterial({ color: 0x64748b, size: 0.6, transparent: true, opacity: 0.5 });
    const starField = new THREE.Points(starGeo, starMat);
    scene.add(starField);

    // Lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(20, 35, 20);
    scene.add(dirLight);

    const cyanLight = new THREE.PointLight(0x38bdf8, 1.0, 60);
    cyanLight.position.set(0, 15, 0);
    scene.add(cyanLight);

    // Selection target ring
    const selRingGeo = new THREE.TorusGeometry(2.1, 0.08, 16, 40);
    const selRingMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const selRing = new THREE.Mesh(selRingGeo, selRingMat);
    selRing.rotation.x = Math.PI / 2;
    selRing.visible = false;
    scene.add(selRing);

    // Objects collection
    const interactiveMeshes = [];
    const haloMeshes = [];
    const flowTracks = []; // { curve, particles: [mesh...], speed, reverse }

    // Helper: Canvas Text Billboard Sprite
    function makeBillboardSprite(title, sub, colorHex, wScale = 6.5, hScale = 3.25) {
      const cvs = document.createElement('canvas');
      cvs.width = 256;
      cvs.height = 128;
      const ctx = cvs.getContext('2d');

      ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
      ctx.strokeStyle = colorHex;
      ctx.lineWidth = 4;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(10, 10, 236, 108, 16);
      } else {
        ctx.rect(10, 10, 236, 108);
      }
      ctx.fill();
      ctx.stroke();

      ctx.font = 'bold 26px sans-serif';
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.fillText(title, 128, 54);

      ctx.font = '20px monospace';
      ctx.fillStyle = colorHex;
      ctx.textAlign = 'center';
      ctx.fillText(sub, 128, 92);

      const tex = new THREE.CanvasTexture(cvs);
      const mat = new THREE.SpriteMaterial({ map: tex, transparent: true });
      const sprite = new THREE.Sprite(mat);
      sprite.scale.set(wScale, hScale, 1);
      return sprite;
    }

    // Helper: Add Flow Curve with Moving Photon Particles
    function addFlowLine(p1, p2, colorHex, particleCount = 6, speed = 0.006, reverse = false, isCurved = true) {
      const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5);
      if (isCurved) {
        mid.y += Math.min(6, p1.distanceTo(p2) * 0.22);
      }
      const curve = new THREE.CatmullRomCurve3([p1.clone(), mid, p2.clone()]);
      const points = curve.getPoints(36);
      const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
      const lineMat = new THREE.LineBasicMaterial({
        color: colorHex,
        transparent: true,
        opacity: 0.35,
        linewidth: 1,
      });
      const line = new THREE.Line(lineGeo, lineMat);
      scene.add(line);

      // Create glowing photon particles along the curve
      const pGroup = [];
      for (let i = 0; i < particleCount; i++) {
        const pGeo = new THREE.SphereGeometry(0.24, 8, 8);
        const pMat = new THREE.MeshBasicMaterial({ color: colorHex });
        const pMesh = new THREE.Mesh(pGeo, pMat);
        pMesh.userData = { t: (i / particleCount) };
        scene.add(pMesh);
        pGroup.push(pMesh);
      }

      flowTracks.push({ curve, particles: pGroup, speed, reverse });
    }

    // 1. Client Node
    const clientPos = new THREE.Vector3(0, 7.5, 19);
    const clientGeo = new THREE.OctahedronGeometry(1.6, 0);
    const clientMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      metalness: 0.8,
      roughness: 0.2,
      emissive: 0x0284c7,
      emissiveIntensity: 0.7,
    });
    const clientMesh = new THREE.Mesh(clientGeo, clientMat);
    clientMesh.position.copy(clientPos);
    scene.add(clientMesh);
    haloMeshes.push({ mesh: clientMesh, rotY: 0.02, rotX: 0.01 });

    const clientSprite = makeBillboardSprite('客户端集群', 'App Clients', '#38bdf8', 5.5, 2.75);
    clientSprite.position.set(clientPos.x, clientPos.y + 3.0, clientPos.z);
    scene.add(clientSprite);

    // 2. Proxy Gateway (fbasecman)
    const proxyWritePort = (topology.proxy && topology.proxy.write_port) || 17432;
    const proxyReadPort = (topology.proxy && topology.proxy.read_port) || 16432;
    const proxyPos = new THREE.Vector3(0, 4.2, 9.5);
    const proxyGeo = new THREE.CylinderGeometry(2.2, 2.2, 1.1, 6);
    const proxyMat = new THREE.MeshStandardMaterial({
      color: 0x10b981,
      metalness: 0.85,
      roughness: 0.2,
      emissive: 0x065f46,
      emissiveIntensity: 0.8,
    });
    const proxyMesh = new THREE.Mesh(proxyGeo, proxyMat);
    proxyMesh.position.copy(proxyPos);
    scene.add(proxyMesh);

    // Rotating orbital ring for proxy
    const proxyRingGeo = new THREE.TorusGeometry(3.0, 0.08, 16, 36);
    const proxyRingMat = new THREE.MeshBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.8 });
    const proxyRing = new THREE.Mesh(proxyRingGeo, proxyRingMat);
    proxyRing.position.copy(proxyPos);
    proxyRing.rotation.x = Math.PI / 2.5;
    scene.add(proxyRing);
    haloMeshes.push({ mesh: proxyRing, rotZ: 0.025 });

    const proxySprite = makeBillboardSprite('fbasecman 网关', `W:${proxyWritePort} / R:${proxyReadPort}`, '#10b981', 6.8, 3.4);
    proxySprite.position.set(proxyPos.x, proxyPos.y + 2.8, proxyPos.z);
    scene.add(proxySprite);

    // Flow from Client to Proxy
    addFlowLine(clientPos, proxyPos, 0x10b981, 7, 0.007, false, false);

    // 3. Dynamic Clusters & Nodes Layout
    const clusters = topology.clusters || [];
    const numClusters = clusters.length;
    const clusterPositions = [];

    if (numClusters === 1) {
      clusterPositions.push(new THREE.Vector3(0, 0, -2));
    } else if (numClusters === 2) {
      clusterPositions.push(new THREE.Vector3(-18, 0, -2));
      clusterPositions.push(new THREE.Vector3(18, 0, -2));
    } else {
      const radius = 20;
      for (let i = 0; i < numClusters; i++) {
        const angle = (i / numClusters) * Math.PI * 2 - Math.PI / 2;
        clusterPositions.push(new THREE.Vector3(radius * Math.cos(angle), 0, radius * Math.sin(angle) - 2));
      }
    }

    const primaryNodes = []; // store for MMR links
    let firstNodeData = null;

    clusters.forEach((cl, cIdx) => {
      const cPos = clusterPositions[cIdx] || new THREE.Vector3(0, 0, 0);
      const isDegraded = (cl.state && cl.state.includes('DEGRADED')) || (cl.state === 'NO_PRIMARY');
      const platformColor = isDegraded ? 0xef4444 : 0x0284c7;

      // Platform Base
      const platGeo = new THREE.CylinderGeometry(8.5, 8.5, 0.5, 36);
      const platMat = new THREE.MeshStandardMaterial({
        color: 0x0f172a,
        metalness: 0.8,
        roughness: 0.3,
        transparent: true,
        opacity: 0.8,
      });
      const platform = new THREE.Mesh(platGeo, platMat);
      platform.position.set(cPos.x, 0.25, cPos.z);
      scene.add(platform);

      // Platform Edge Glow Ring
      const ringGeo = new THREE.TorusGeometry(8.55, 0.08, 16, 64);
      const ringMat = new THREE.MeshBasicMaterial({ color: platformColor });
      const platRing = new THREE.Mesh(ringGeo, ringMat);
      platRing.position.set(cPos.x, 0.5, cPos.z);
      platRing.rotation.x = Math.PI / 2;
      scene.add(platRing);

      // Cluster Title Billboard
      const clusterSprite = makeBillboardSprite(
        cl.label || cl.name,
        `主库: ${cl.primary || '-'} [${cl.state || 'VALID'}]`,
        isDegraded ? '#ef4444' : '#38bdf8',
        7.2,
        3.6
      );
      clusterSprite.position.set(cPos.x, 6.0, cPos.z - 6.5);
      scene.add(clusterSprite);

      // Distribute Nodes
      const nodes = cl.nodes || [];
      const numNodes = nodes.length;
      let primaryNodeInCluster = null;

      nodes.forEach((n, nIdx) => {
        if (!firstNodeData) firstNodeData = n;
        const role = (n.role || 'STANDBY').toUpperCase();
        const isPrimary = role === 'PRIMARY';
        const isParted = role === 'PARTED' || role === 'OFFLINE' || n.state === 'OFFLINE';

        let nx = cPos.x;
        let nz = cPos.z;
        if (numNodes === 1) {
          nx = cPos.x;
          nz = cPos.z;
        } else if (numNodes === 2) {
          nx = cPos.x + (nIdx === 0 ? -3.4 : 3.4);
          nz = cPos.z;
        } else {
          if (isPrimary) {
            nx = cPos.x;
            nz = cPos.z + 2.8;
          } else {
            const side = (nIdx % 2 === 1) ? -1 : 1;
            const step = Math.ceil(nIdx / 2);
            nx = cPos.x + side * (2.8 * step);
            nz = cPos.z - 2.2;
          }
        }
        const nodePos = new THREE.Vector3(nx, 1.5, nz);

        // Node Chassis Cylinder
        const nodeGeo = new THREE.CylinderGeometry(1.55, 1.55, 2.4, 24);
        let nodeMat;
        if (isParted) {
          nodeMat = new THREE.MeshStandardMaterial({
            color: 0xef4444,
            wireframe: true,
            emissive: 0x7f1d1d,
            emissiveIntensity: 0.9,
          });
        } else {
          nodeMat = new THREE.MeshStandardMaterial({
            color: 0x1e293b,
            metalness: 0.88,
            roughness: 0.22,
          });
        }
        const nodeMesh = new THREE.Mesh(nodeGeo, nodeMat);
        nodeMesh.position.copy(nodePos);
        nodeMesh.userData = { node: n, cluster: cl, label: n.label || n.name };
        scene.add(nodeMesh);
        interactiveMeshes.push(nodeMesh);

        // Waist Status LED Ring
        const ledColor = isParted ? 0xef4444 : (isPrimary ? 0x10b981 : 0x38bdf8);
        const ledGeo = new THREE.TorusGeometry(1.58, 0.12, 16, 32);
        const ledMat = new THREE.MeshBasicMaterial({ color: ledColor });
        const ledMesh = new THREE.Mesh(ledGeo, ledMat);
        ledMesh.position.copy(nodePos);
        ledMesh.rotation.x = Math.PI / 2;
        scene.add(ledMesh);

        // Primary Golden/Emerald Crown Halo
        if (isPrimary && !isParted) {
          primaryNodeInCluster = { pos: nodePos, node: n };
          primaryNodes.push({ pos: nodePos, node: n, cluster: cl });

          const crownGeo = new THREE.TorusGeometry(1.3, 0.12, 16, 32);
          const crownMat = new THREE.MeshStandardMaterial({
            color: 0x10b981,
            metalness: 0.9,
            roughness: 0.1,
            emissive: 0x059669,
            emissiveIntensity: 0.8,
          });
          const crown = new THREE.Mesh(crownGeo, crownMat);
          crown.position.set(nodePos.x, nodePos.y + 2.0, nodePos.z);
          crown.rotation.x = Math.PI / 2;
          scene.add(crown);
          haloMeshes.push({ mesh: crown, rotY: 0.03 });

          // Traffic: Proxy -> Primary (Emerald Write Flow)
          addFlowLine(proxyPos, nodePos, 0x10b981, 7, 0.007, false, true);
        } else if (!isParted) {
          // Traffic: Proxy -> Standby (Cyan Read Flow)
          addFlowLine(proxyPos, nodePos, 0x38bdf8, 5, 0.005, false, true);
        }

        // Node Label Billboard
        let roleName = isPrimary ? '👑 PRIMARY' : (isParted ? '⚠️ PARTED' : '🔄 STANDBY');
        let roleColor = isParted ? '#ef4444' : (isPrimary ? '#10b981' : '#38bdf8');
        const nodeSprite = makeBillboardSprite(
          `${n.label || n.name} (${n.name})`,
          `${roleName} :${n.port || '-'}`,
          roleColor,
          5.6,
          2.8
        );
        nodeSprite.position.set(nodePos.x, nodePos.y + 3.1, nodePos.z);
        scene.add(nodeSprite);
      });

      // Internal Cluster Streaming Replication Links (Primary -> Standbys)
      if (primaryNodeInCluster) {
        nodes.forEach((n) => {
          if (n.role !== 'PRIMARY') {
            const role = (n.role || 'STANDBY').toUpperCase();
            const isParted = role === 'PARTED' || role === 'OFFLINE' || n.state === 'OFFLINE';
            const standbyMesh = interactiveMeshes.find((m) => m.userData.node === n);
            if (standbyMesh) {
              if (isParted) {
                // Red broken dashed link
                const p1 = primaryNodeInCluster.pos.clone();
                const p2 = standbyMesh.position.clone();
                const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5);
                mid.y += 1.8;
                const curve = new THREE.CatmullRomCurve3([p1, mid, p2]);
                const lineGeo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(24));
                const lineMat = new THREE.LineDashedMaterial({
                  color: 0xef4444,
                  dashSize: 0.8,
                  gapSize: 0.6,
                  linewidth: 2,
                });
                const line = new THREE.Line(lineGeo, lineMat);
                line.computeLineDistances();
                scene.add(line);
              } else {
                // Active cyan WAL streaming pulse
                addFlowLine(primaryNodeInCluster.pos, standbyMesh.position, 0x38bdf8, 5, 0.006, false, true);
              }
            }
          }
        });
      }
    });

    // 4. Inter-Cluster MMR Highway (Connecting Primary A <===> Primary B)
    if (primaryNodes.length >= 2) {
      for (let i = 0; i < primaryNodes.length; i++) {
        for (let j = i + 1; j < primaryNodes.length; j++) {
          const pA = primaryNodes[i].pos;
          const pB = primaryNodes[j].pos;

          // Bidirectional MMR plasma flows
          addFlowLine(pA, pB, 0xc084fc, 8, 0.006, false, true);
          addFlowLine(pA, pB, 0xc084fc, 8, 0.006, true, true);

          // Center MMR Label Sprite
          const midMMR = new THREE.Vector3().addVectors(pA, pB).multiplyScalar(0.5);
          midMMR.y += 4.5;
          const mmrSprite = makeBillboardSprite('MMR 双向对等高速公路', 'Bidirectional Sync', '#c084fc', 6.8, 3.4);
          mmrSprite.position.copy(midMMR);
          scene.add(mmrSprite);
        }
      }
    }

    // Default Node Selection Inspector
    if (firstNodeData) {
      updateTopologyInspector(firstNodeData.name || firstNodeData.label, topology);
    }

    // -----------------------------------------------------------------------
    // Interaction: Raycasting & Click Selection
    // -----------------------------------------------------------------------
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    function onPointerClick(event) {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(interactiveMeshes, false);

      if (intersects.length > 0) {
        const hit = intersects[0].object;
        const nData = hit.userData.node;
        if (nData) {
          selRing.position.copy(hit.position);
          selRing.position.y = 0.2;
          selRing.visible = true;
          updateTopologyInspector(nData.name || nData.label, topology);
          controls.target.set(hit.position.x, hit.position.y, hit.position.z);
        }
      }
    }

    renderer.domElement.addEventListener('click', onPointerClick);

    // -----------------------------------------------------------------------
    // Toolbar Controls Handlers
    // -----------------------------------------------------------------------
    const btnReset = document.getElementById('btn3dReset');
    if (btnReset) {
      btnReset.addEventListener('click', () => {
        camera.position.set(0, 26, 44);
        controls.target.set(0, 2, 0);
        controls.update();
      });
    }

    const btnTop = document.getElementById('btn3dTop');
    if (btnTop) {
      btnTop.addEventListener('click', () => {
        camera.position.set(0, 58, 2);
        controls.target.set(0, 0, 0);
        controls.update();
      });
    }

    const btnRotate = document.getElementById('btn3dRotate');
    if (btnRotate) {
      btnRotate.addEventListener('click', () => {
        controls.autoRotate = !controls.autoRotate;
        btnRotate.classList.toggle('active', controls.autoRotate);
      });
    }

    // Resize Handler
    function onResize() {
      if (!canvasContainer || !renderer) return;
      const w = canvasContainer.clientWidth;
      if (w > 100) {
        camera.aspect = w / height;
        camera.updateProjectionMatrix();
        renderer.setSize(w, height);
      }
    }

    const resizeObserver = new ResizeObserver(() => onResize());
    resizeObserver.observe(canvasContainer);

    // -----------------------------------------------------------------------
    // Animation Render Loop
    // -----------------------------------------------------------------------
    let animId = null;
    function animate() {
      animId = requestAnimationFrame(animate);

      // Rotate halos & crowns
      haloMeshes.forEach((item) => {
        if (item.rotY) item.mesh.rotation.y += item.rotY;
        if (item.rotX) item.mesh.rotation.x += item.rotX;
        if (item.rotZ) item.mesh.rotation.z += item.rotZ;
      });

      // Update particle photon positions along flow tracks
      flowTracks.forEach((track) => {
        const { curve, particles, speed, reverse } = track;
        particles.forEach((pMesh) => {
          if (reverse) {
            pMesh.userData.t -= speed;
            if (pMesh.userData.t < 0) pMesh.userData.t += 1.0;
          } else {
            pMesh.userData.t += speed;
            if (pMesh.userData.t > 1.0) pMesh.userData.t -= 1.0;
          }
          const pt = curve.getPointAt(pMesh.userData.t);
          pMesh.position.copy(pt);
        });
      });

      controls.update();
      renderer.render(scene, camera);
    }

    animId = requestAnimationFrame(animate);

    active3dContext = {
      animId,
      renderer,
      resizeObserver,
      onResize,
    };
  }

  // -------------------------------------------------------------------------
  // -------------------------------------------------------------------------
  // Dynamic 2D SVG Vector Topology Diagram with Test Process Animation Player
  // -------------------------------------------------------------------------
  let activeTopoAnim = {
    isPlaying: false,
    currentStepIndex: 0,
    timer: null,
    speed: 1, // 1x, 1.5x, 2x
  };

  function cleanUpTopologyAnimation() {
    if (activeTopoAnim.timer) {
      clearInterval(activeTopoAnim.timer);
      activeTopoAnim.timer = null;
    }
    activeTopoAnim.isPlaying = false;
  }

  function renderTopologyGraphHtml(topology, stepIndex = 0) {
    const snapshots = topology.step_snapshots || [];
    const hasSnapshots = snapshots.length > 0;
    const currentSnap = hasSnapshots ? (snapshots[stepIndex] || snapshots[0]) : null;

    const clusters = (currentSnap && currentSnap.clusters) || topology.clusters || [];
    const links = (currentSnap && currentSnap.links) || {
      write_a: true,
      read_a: true,
      read_b: true,
      wal_a: true,
      wal_b: true,
      mmr: true,
    };

    const proxyWritePort = (topology.proxy && topology.proxy.write_port) || 17432;
    const proxyReadPort = (topology.proxy && topology.proxy.read_port) || 16432;

    const isDual = clusters.length === 2;
    const clA = clusters[0] || { name: 'Cluster 1', nodes: [] };
    const clB = clusters[1] || { name: 'Cluster 2', nodes: [] };

    const priA = (clA.nodes || []).find((n) => n.role === 'PRIMARY') || (clA.nodes || [])[0] || {};
    const stdA = (clA.nodes || []).find((n) => n.role !== 'PRIMARY') || {};

    const priB = (clB.nodes || []).find((n) => n.role === 'PRIMARY') || (clB.nodes || [])[0] || {};
    const stdB = (clB.nodes || []).find((n) => n.role !== 'PRIMARY') || {};

    const isStdAParted = stdA.role === 'PARTED' || stdA.state === 'OFFLINE' || !links.read_a;
    const isStdBParted = stdB.role === 'PARTED' || stdB.state === 'OFFLINE' || !links.read_b;

    // fbasecman proxy dynamic perception states
    const proxyState = (currentSnap && currentSnap.proxy_state) || {
      status: 'HEALTHY',
      badge: '探活就绪',
      perception_title: '探活就绪',
      monitor_status: '全部节点探活正常',
      topology_status: 'Site A: VALID | Site B: VALID',
      routing_decision: '写->A0(10011) | 读->A1(10012)',
    };
    const proxyStatus = proxyState.status || 'HEALTHY';
    const isDegraded = proxyStatus === 'DEGRADED';
    const isDebounce = proxyStatus === 'DEBOUNCING';
    const isRecovery = proxyStatus === 'RECOVERED';

    let proxyBg = '#07241a';
    let proxyStroke = '#10b981';
    let proxyFilter = 'url(#glow-primary)';
    let proxyBadgeBg = '#064e3b';
    let proxyBadgeStroke = '#10b981';
    let proxyBadgeText = '#a7f3d0';
    let proxyBadgeIcon = '🟢';

    if (isDegraded) {
      proxyBg = '#2a0808';
      proxyStroke = '#ef4444';
      proxyFilter = 'url(#glow-parted)';
      proxyBadgeBg = '#7f1d1d';
      proxyBadgeStroke = '#ef4444';
      proxyBadgeText = '#fecaca';
      proxyBadgeIcon = '🚨';
    } else if (isDebounce) {
      proxyBg = '#291804';
      proxyStroke = '#f59e0b';
      proxyFilter = 'url(#glow-debounce)';
      proxyBadgeBg = '#78350f';
      proxyBadgeStroke = '#f59e0b';
      proxyBadgeText = '#fde68a';
      proxyBadgeIcon = '⚡';
    } else if (isRecovery) {
      proxyBg = '#07241a';
      proxyStroke = '#10b981';
      proxyFilter = 'url(#glow-primary)';
      proxyBadgeBg = '#064e3b';
      proxyBadgeStroke = '#10b981';
      proxyBadgeText = '#a7f3d0';
      proxyBadgeIcon = '✨';
    }

    return `
      <div class="topology-graph-wrapper">
        ${hasSnapshots ? `
          <!-- Test Process Animation HUD Controller -->
          <div class="topo-anim-panel">
            <div class="anim-toolbar">
              <div class="anim-btn-group">
                <button class="anim-btn" id="btnTopoAnimPrev" title="上一步 (快捷键 ←)">⏮️ 上一步</button>
                <button class="anim-btn ${activeTopoAnim.isPlaying ? 'playing' : 'primary'}" id="btnTopoAnimPlay" title="自动播放/暂停">
                  ${activeTopoAnim.isPlaying ? '⏸️ 暂停演示' : '▶️ 播放过程演示'}
                </button>
                <button class="anim-btn" id="btnTopoAnimNext" title="下一步 (快捷键 →)">⏭️ 下一步</button>
                <button class="anim-btn" id="btnTopoAnimReset" title="重置到第一步">🔄 重头演示</button>
              </div>

              <div class="anim-speed-selector">
                <span class="speed-label">倍速:</span>
                <button class="speed-btn ${activeTopoAnim.speed === 1 ? 'active' : ''}" data-speed="1">1x</button>
                <button class="speed-btn ${activeTopoAnim.speed === 1.5 ? 'active' : ''}" data-speed="1.5">1.5x</button>
                <button class="speed-btn ${activeTopoAnim.speed === 2 ? 'active' : ''}" data-speed="2">2x</button>
              </div>

              <div class="anim-step-badge-wrap">
                <span class="anim-badge-num">步骤 ${currentSnap.step_num} / ${snapshots.length}</span>
                <span class="anim-badge-type ${currentSnap.event_type || 'normal'}">${(currentSnap.event_type || 'NORMAL').toUpperCase()}</span>
              </div>
            </div>

            <!-- Timeline Step Pills -->
            <div class="anim-timeline-bar">
              ${snapshots.map((s, idx) => {
                const isActive = idx === stepIndex;
                const eType = s.event_type || 'normal';
                let icon = '⚪';
                if (eType === 'fault') icon = '🚨';
                else if (eType === 'recovery') icon = '🔄';
                else if (eType === 'debounce') icon = '⚡';
                else if (eType === 'init') icon = '🚀';
                else icon = '📊';

                return `
                  <button class="timeline-pill ${isActive ? 'active' : ''} ${eType}" data-step-idx="${idx}">
                    <span class="pill-icon">${icon}</span>
                    <span class="pill-num">步骤 ${s.step_num}</span>
                    <span class="pill-title">${escapeHtml(s.title.replace(/^步骤\s*\d+:\s*/, ''))}</span>
                  </button>
                `;
              }).join('')}
            </div>

            <!-- Dynamic HUD Step Banner with fbasecman Perception Radar -->
            <div class="anim-step-banner ${currentSnap.event_type || ''}">
              <div class="banner-header">
                <span class="banner-title">${escapeHtml(currentSnap.title)}</span>
                ${currentSnap.action ? `<span class="banner-action"><strong class="banner-tag">测试动作</strong> ${escapeHtml(currentSnap.action)}</span>` : ''}
              </div>
              ${currentSnap.event_desc ? `<div class="banner-desc">${escapeHtml(currentSnap.event_desc)}</div>` : ''}
              
              <!-- fbasecman 3-Dimension Perception HUD -->
              <div class="banner-proxy-radar">
                <span class="radar-tag monitor ${proxyStatus}"><strong class="tag-title">🔍 探活感知</strong> ${escapeHtml(proxyState.monitor_status || '正常')}</span>
                <span class="radar-tag topo ${proxyStatus}"><strong class="tag-title">🏢 拓扑状态</strong> ${escapeHtml(proxyState.topology_status || 'VALID')}</span>
                <span class="radar-tag route ${proxyStatus}"><strong class="tag-title">🔀 路由决策</strong> ${escapeHtml(proxyState.routing_decision || '正常分流')}</span>
              </div>
            </div>
          </div>
        ` : ''}

        <svg class="topology-svg-canvas" viewBox="0 0 920 475" fill="none" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <marker id="arrow-write" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <polygon points="0 0, 6 3, 0 6" fill="#10b981" />
            </marker>
            <marker id="arrow-read" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <polygon points="0 0, 6 3, 0 6" fill="#38bdf8" />
            </marker>
            <marker id="arrow-mmr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <polygon points="0 0, 6 3, 0 6" fill="#c084fc" />
            </marker>
            <marker id="arrow-rep" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <polygon points="0 0, 6 3, 0 6" fill="#38bdf8" />
            </marker>
            <filter id="glow-primary" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="5" flood-color="#10b981" flood-opacity="0.65" />
            </filter>
            <filter id="glow-debounce" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#f59e0b" flood-opacity="0.75" />
            </filter>
            <filter id="glow-standby" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="5" flood-color="#38bdf8" flood-opacity="0.45" />
            </filter>
            <filter id="glow-parted" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="8" flood-color="#ef4444" flood-opacity="0.85" />
            </filter>
          </defs>

          <!-- LAYER 1: Flow Lines -->
          <!-- Client -> Proxy Write -->
          <path d="M 435 50 L 435 88" class="flow-write" stroke-width="2.8" marker-end="url(#arrow-write)" />
          <!-- Client -> Proxy Read -->
          <path d="M 485 50 L 485 88" class="flow-read" stroke-width="2.4" marker-end="url(#arrow-read)" />

          <!-- Proxy -> Primary A Write -->
          <path d="M 360 168 C 290 190, 240 225, 240 255" class="flow-write" stroke-width="2.8" marker-end="url(#arrow-write)" />

          <!-- Proxy -> Standby A Read (Active or Broken) -->
          ${links.read_a ? `
            <path d="M 320 168 C 210 190, 110 305, 110 360" class="flow-read" stroke-width="2.4" marker-end="url(#arrow-read)" />
          ` : `
            <path d="M 320 168 C 210 190, 110 305, 110 360" class="flow-broken" stroke-width="2" />
            <g transform="translate(195, 255)" class="link-break-marker">
              <rect x="-44" y="-11" width="88" height="22" rx="11" fill="#450a0a" stroke="#ef4444" stroke-width="1.2" />
              <text x="0" y="4" fill="#fca5a5" font-size="10" font-weight="bold" text-anchor="middle">❌ 读路由切断</text>
            </g>
          `}

          <!-- fbasecman Monitor Probe Link to A1 -->
          ${isDegraded ? `
            <path d="M 270 168 Q 175 235, 125 360" stroke="#ef4444" stroke-width="2" stroke-dasharray="4 4" class="flow-probe" />
            <g transform="translate(180, 228)" class="probe-tag">
              <rect x="-46" y="-9" width="92" height="18" rx="9" fill="#7f1d1d" stroke="#ef4444" stroke-width="1" />
              <text x="0" y="3.5" fill="#fecaca" font-size="8.5" font-weight="bold" text-anchor="middle">🚨 探活 3/3 失败</text>
            </g>
          ` : (isDebounce ? `
            <path d="M 270 168 Q 175 235, 125 360" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="4 3" class="flow-probe" />
            <g transform="translate(180, 228)" class="probe-tag">
              <rect x="-46" y="-9" width="92" height="18" rx="9" fill="#78350f" stroke="#f59e0b" stroke-width="1" />
              <text x="0" y="3.5" fill="#fde68a" font-size="8.5" font-weight="bold" text-anchor="middle">⚡ 探活 1/3 (防抖)</text>
            </g>
          ` : (isRecovery ? `
            <path d="M 270 168 Q 175 235, 125 360" stroke="#10b981" stroke-width="1.8" stroke-dasharray="4 3" class="flow-probe" />
            <g transform="translate(180, 228)" class="probe-tag">
              <rect x="-46" y="-9" width="92" height="18" rx="9" fill="#064e3b" stroke="#10b981" stroke-width="1" />
              <text x="0" y="3.5" fill="#a7f3d0" font-size="8.5" font-weight="bold" text-anchor="middle">✅ 探活 3/3 成功</text>
            </g>
          ` : `
            <path d="M 270 168 Q 175 235, 125 360" stroke="#10b981" stroke-width="1" stroke-dasharray="3 5" opacity="0.3" />
          `))}

          ${isDual ? `
            <!-- Proxy -> Standby B Read -->
            ${links.read_b ? `
              <path d="M 600 168 C 710 190, 810 305, 810 360" class="flow-read" stroke-width="2.4" marker-end="url(#arrow-read)" />
            ` : `
              <path d="M 600 168 C 710 190, 810 305, 810 360" class="flow-broken" stroke-width="2" />
            `}

            <!-- MMR Bidirectional Highway -->
            <path d="M 300 290 C 420 260, 500 260, 620 290" class="flow-mmr" stroke-width="3" marker-end="url(#arrow-mmr)" />
            <path d="M 620 300 C 500 270, 420 270, 300 300" class="flow-mmr" stroke-width="3" marker-end="url(#arrow-mmr)" />
            <rect x="400" y="268" width="120" height="22" rx="11" fill="#181329" stroke="#c084fc" stroke-width="1.2" />
            <text x="460" y="283" fill="#d8b4fe" font-size="10" font-weight="bold" text-anchor="middle">MMR 双向流复制</text>
          ` : ''}

          <!-- Streaming WAL: Primary A -> Standby A -->
          ${links.wal_a ? `
            <path d="M 210 325 C 210 360, 140 350, 140 365" class="flow-rep" stroke-width="2.2" marker-end="url(#arrow-rep)" />
          ` : `
            <path d="M 210 325 C 210 360, 140 350, 140 365" class="flow-broken" stroke-width="2" />
            <g transform="translate(175, 345)">
              <rect x="-36" y="-9" width="72" height="18" rx="9" fill="#450a0a" stroke="#ef4444" stroke-width="1" />
              <text x="0" y="3.5" fill="#fca5a5" font-size="9" font-weight="bold" text-anchor="middle">⚡ 复制中断</text>
            </g>
          `}

          ${isDual ? `
            <!-- Streaming WAL: Primary B -> Standby B -->
            ${links.wal_b ? `
              <path d="M 710 325 C 710 360, 780 350, 780 365" class="flow-rep" stroke-width="2.2" marker-end="url(#arrow-rep)" />
            ` : `
              <path d="M 710 325 C 710 360, 780 350, 780 365" class="flow-broken" stroke-width="2" />
            `}
          ` : ''}

          <!-- LAYER 2: Client & Proxy Nodes -->
          <!-- Client App -->
          <g transform="translate(370, 10)">
            <rect width="180" height="40" rx="8" fill="#111c2e" stroke="#38bdf8" stroke-width="1.5" />
            <text x="90" y="22" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">💻 客户端应用 (App)</text>
            <text x="90" y="34" fill="#94a3b8" font-size="9" text-anchor="middle">写: 17432 | 读: 16432</text>
          </g>

          <!-- fbasecman Proxy Gateway (Dynamic Perception Core) -->
          <g transform="translate(230, 88)" class="proxy-gateway-g ${proxyStatus}">
            ${isDegraded ? `<rect class="proxy-shockwave" x="-4" y="-4" width="468" height="88" rx="14" fill="none" stroke="#ef4444" stroke-width="2" opacity="0.6" />` : ''}
            ${isDebounce ? `<rect class="proxy-shockwave-amber" x="-4" y="-4" width="468" height="88" rx="14" fill="none" stroke="#f59e0b" stroke-width="2" opacity="0.6" />` : ''}
            
            <rect class="proxy-main-rect" width="460" height="80" rx="10" fill="${proxyBg}" stroke="${proxyStroke}" stroke-width="${isDegraded ? '2.5' : '2'}" filter="${proxyFilter}" />
            
            <!-- Row 1: Title, Ports & Status Badge -->
            <text x="15" y="24" fill="#f8fafc" font-size="13" font-weight="bold">⚡ fbasecman 代理网关</text>
            <text x="180" y="24" fill="#94a3b8" font-size="10.5">W:${proxyWritePort} / R:${proxyReadPort}</text>
            
            <!-- Status Badge on Right -->
            <g transform="translate(380, 20)" class="proxy-badge-g">
              <rect x="-65" y="-11" width="130" height="22" rx="11" fill="${proxyBadgeBg}" stroke="${proxyBadgeStroke}" stroke-width="1.2" />
              <text x="0" y="4" fill="${proxyBadgeText}" font-size="9.5" font-weight="bold" text-anchor="middle">${proxyBadgeIcon} ${escapeHtml(proxyState.badge)}</text>
            </g>

            <line x1="12" y1="34" x2="448" y2="34" stroke="${proxyStroke}" stroke-opacity="0.25" stroke-width="1" />
            
            <!-- Row 2: Monitor Probe Perception -->
            <text x="15" y="50" fill="#e2e8f0" font-size="10" font-weight="600">🔍 探活感知: <tspan fill="${isDegraded ? '#fca5a5' : (isDebounce ? '#fde68a' : '#86efac')}">${escapeHtml(proxyState.monitor_status || '全部正常')}</tspan></text>
            
            <!-- Row 3: Topology State & Routing Decision -->
            <text x="15" y="68" fill="#e2e8f0" font-size="10" font-weight="600">🏢 拓扑感知: <tspan fill="${isDegraded ? '#fca5a5' : '#38bdf8'}">${escapeHtml(proxyState.topology_status || '')}</tspan> | 🔀 <tspan fill="${isDegraded ? '#fca5a5' : '#a7f3d0'}">${escapeHtml(proxyState.routing_decision || '')}</tspan></text>
          </g>

          <!-- LAYER 3: Clusters Boundaries -->
          <!-- Cluster A Boundary -->
          <g transform="translate(40, 205)">
            <rect width="360" height="255" rx="14" fill="#0f172a" fill-opacity="0.8" stroke="${clA.state && clA.state.includes('DEGRADED') ? '#f59e0b' : '#38bdf8'}" stroke-width="${clA.state && clA.state.includes('DEGRADED') ? '2' : '1.5'}" stroke-dasharray="${clA.state && clA.state.includes('DEGRADED') ? '8 4' : '6 4'}" class="${clA.state && clA.state.includes('DEGRADED') ? 'cluster-degraded-rect' : ''}" />
            <rect x="20" y="-13" width="160" height="26" rx="6" fill="#1e293b" stroke="${clA.state && clA.state.includes('DEGRADED') ? '#f59e0b' : '#38bdf8'}" stroke-width="1.2" />
            <text x="100" y="4" fill="#f8fafc" font-size="11.5" font-weight="bold" text-anchor="middle">🏢 ${escapeHtml(clA.label || clA.name)}</text>
            <text x="280" y="14" fill="${clA.state && clA.state.includes('DEGRADED') ? '#fbbf24' : '#34d399'}" font-size="10.5" font-weight="bold">${escapeHtml(clA.state || 'VALID')}</text>
          </g>

          ${isDual ? `
            <!-- Cluster B Boundary -->
            <g transform="translate(520, 205)">
              <rect width="360" height="255" rx="14" fill="#0f172a" fill-opacity="0.8" stroke="${clB.state && clB.state.includes('DEGRADED') ? '#f59e0b' : '#38bdf8'}" stroke-width="${clB.state && clB.state.includes('DEGRADED') ? '2' : '1.5'}" stroke-dasharray="${clB.state && clB.state.includes('DEGRADED') ? '8 4' : '6 4'}" class="${clB.state && clB.state.includes('DEGRADED') ? 'cluster-degraded-rect' : ''}" />
              <rect x="20" y="-13" width="160" height="26" rx="6" fill="#1e293b" stroke="${clB.state && clB.state.includes('DEGRADED') ? '#f59e0b' : '#38bdf8'}" stroke-width="1.2" />
              <text x="100" y="4" fill="#f8fafc" font-size="11.5" font-weight="bold" text-anchor="middle">🏢 ${escapeHtml(clB.label || clB.name)}</text>
              <text x="280" y="14" fill="${clB.state && clB.state.includes('DEGRADED') ? '#fbbf24' : '#34d399'}" font-size="10.5" font-weight="bold">${escapeHtml(clB.state || 'VALID')}</text>
            </g>
          ` : ''}

          <!-- LAYER 4: Database Nodes -->
          <!-- Node A0 (Primary) -->
          ${renderSvgNode(priA, 180, 255, priA.role === 'PRIMARY' ? 'primary' : (priA.role === 'PARTED' ? 'parted' : 'standby'), currentSnap)}

          <!-- Node A1 (Standby or Parted) -->
          ${renderSvgNode(stdA, 60, 360, isStdAParted ? 'parted' : (stdA.role === 'PRIMARY' ? 'primary' : 'standby'), currentSnap)}

          ${isDual ? `
            <!-- Node B0 (Primary) -->
            ${renderSvgNode(priB, 660, 255, priB.role === 'PRIMARY' ? 'primary' : (priB.role === 'PARTED' ? 'parted' : 'standby'), currentSnap)}

            <!-- Node B1 (Standby or Parted) -->
            ${renderSvgNode(stdB, 760, 360, isStdBParted ? 'parted' : (stdB.role === 'PRIMARY' ? 'primary' : 'standby'), currentSnap)}
          ` : ''}
        </svg>
      </div>
      <div class="topology-inspector" id="topoInspector">
        <!-- Populated dynamically on click -->
      </div>
      <div class="topology-legend">
        <div class="legend-item"><span class="legend-line write"></span> 绿色流线: 客户端写请求路由 (Primary)</div>
        <div class="legend-item"><span class="legend-line read"></span> 蓝色流线: 客户端只读负载分流 (Standby)</div>
        <div class="legend-item"><span class="legend-line mmr"></span> 紫色流线: 多主中心 MMR 双向对等复制链路</div>
        <div class="legend-item"><span class="legend-line rep"></span> 浅蓝流线: 集群内部流复制 (WAL Stream)</div>
        <div class="legend-item"><span class="legend-line parted"></span> 红色虚线: 节点故障离线 / 路由剔除</div>
      </div>
    `;
  }

  function renderSvgNode(n, x, y, type, currentSnap = null) {
    if (!n || !n.name) return '';
    const isPrimary = type === 'primary' || n.role === 'PRIMARY';
    const isParted = type === 'parted' || n.role === 'PARTED' || n.state === 'OFFLINE';

    const bgFill = isPrimary ? '#064e3b' : (isParted ? '#450a0a' : '#1e3a8a');
    const strokeColor = isPrimary ? '#10b981' : (isParted ? '#ef4444' : '#38bdf8');
    const filter = isPrimary ? 'url(#glow-primary)' : (isParted ? 'url(#glow-parted)' : 'url(#glow-standby)');
    const roleIcon = isPrimary ? '👑 主库' : (isParted ? '⚠️ 隔离' : '🔄 从库');
    const stateText = isParted ? 'OFFLINE (已剔除)' : (n.state || 'ACTIVE');

    let badgeHtml = '';
    if (isParted) {
      badgeHtml = `
        <g transform="translate(65, -8)">
          <rect x="-35" y="-10" width="70" height="20" rx="10" fill="#7f1d1d" stroke="#ef4444" stroke-width="1.2" />
          <text x="0" y="4" fill="#fecaca" font-size="9.5" font-weight="bold" text-anchor="middle">🚨 已剔除</text>
        </g>
      `;
    } else if (currentSnap && currentSnap.event_type === 'recovery') {
      badgeHtml = `
        <g transform="translate(65, -8)">
          <rect x="-38" y="-10" width="76" height="20" rx="10" fill="#064e3b" stroke="#10b981" stroke-width="1.2" />
          <text x="0" y="4" fill="#a7f3d0" font-size="9.5" font-weight="bold" text-anchor="middle">✨ 重新准入</text>
        </g>
      `;
    }

    return `
      <g class="topo-node-g ${isParted ? 'node-parted' : ''}" data-node-id="${escapeHtml(n.name || n.label)}" transform="translate(${x}, ${y})">
        ${isParted ? `<circle cx="65" cy="37" r="48" fill="none" stroke="#ef4444" stroke-width="2" opacity="0.6" class="node-shockwave" />` : ''}
        <rect class="node-main-rect" width="130" height="74" rx="10" fill="${bgFill}" stroke="${strokeColor}" stroke-width="${isParted ? '2.5' : '2'}" filter="${filter}" />
        <ellipse cx="65" cy="12" rx="45" ry="6" fill="#1e293b" stroke="${strokeColor}" stroke-width="1" />
        <text x="65" y="36" fill="#f8fafc" font-size="13" font-weight="bold" text-anchor="middle">${escapeHtml(n.label || n.name)}</text>
        <text x="65" y="52" fill="${strokeColor}" font-size="10.5" font-weight="bold" text-anchor="middle">${roleIcon} :${escapeHtml(String(n.port || '-'))}</text>
        <text x="65" y="66" fill="#cbd5e1" font-size="9" text-anchor="middle">${escapeHtml(stateText)}</text>
        ${badgeHtml}
      </g>
    `;
  }

  function bindTopologyGraphEvents(topology) {
    const snapshots = topology.step_snapshots || [];

    const btnPlay = document.getElementById('btnTopoAnimPlay');
    const btnPrev = document.getElementById('btnTopoAnimPrev');
    const btnNext = document.getElementById('btnTopoAnimNext');
    const btnReset = document.getElementById('btnTopoAnimReset');
    const speedBtns = document.querySelectorAll('.anim-speed-selector .speed-btn');
    const pills = document.querySelectorAll('.anim-timeline-bar .timeline-pill');

    function updateStepView(newIdx) {
      activeTopoAnim.currentStepIndex = newIdx;
      modalTopologyContainer.innerHTML = renderTopologyGraphHtml(topology, activeTopoAnim.currentStepIndex);
      bindTopologyGraphEvents(topology);

      // GSAP Hardware-Accelerated Dynamic Choreographed Transitions
      if (window.gsap) {
        const tl = gsap.timeline();

        // 1. Step banner & radar tags stagger
        tl.fromTo('.anim-step-banner', 
          { opacity: 0, y: -8 }, 
          { opacity: 1, y: 0, duration: 0.3, ease: 'power2.out' }, 0
        );
        tl.fromTo('.radar-tag',
          { opacity: 0, scale: 0.85, y: 4 },
          { opacity: 1, scale: 1, y: 0, duration: 0.3, stagger: 0.08, ease: 'back.out(1.7)' }, 0.05
        );

        // 2. Proxy Gateway & Badge
        const currentSnap = snapshots[newIdx];
        const proxyStatus = currentSnap && currentSnap.proxy_state && currentSnap.proxy_state.status;

        tl.fromTo('.proxy-badge-g',
          { scale: 0.5, opacity: 0 },
          { scale: 1, opacity: 1, duration: 0.35, ease: 'back.out(2)' }, 0.1
        );

        if (proxyStatus === 'DEGRADED') {
          tl.fromTo('.proxy-main-rect', 
            { stroke: '#ffffff', strokeWidth: 4 }, 
            { stroke: '#ef4444', strokeWidth: 2.5, duration: 0.25, repeat: 3, yoyo: true }, 0.1
          );
          tl.fromTo('.proxy-shockwave',
            { scale: 0.96, opacity: 1 },
            { scale: 1.06, opacity: 0, duration: 0.8, repeat: 2, ease: 'power1.out' }, 0.2
          );
        } else if (proxyStatus === 'DEBOUNCING') {
          tl.fromTo('.proxy-main-rect', 
            { stroke: '#ffffff', strokeWidth: 3.5 }, 
            { stroke: '#f59e0b', strokeWidth: 2.2, duration: 0.25, repeat: 2, yoyo: true }, 0.1
          );
          tl.fromTo('.proxy-shockwave-amber',
            { scale: 0.96, opacity: 1 },
            { scale: 1.06, opacity: 0, duration: 0.8, repeat: 2, ease: 'power1.out' }, 0.2
          );
        } else if (proxyStatus === 'RECOVERED') {
          tl.fromTo('.proxy-main-rect', 
            { stroke: '#6ee7b7', strokeWidth: 3.5 }, 
            { stroke: '#10b981', strokeWidth: 2.2, duration: 0.35, repeat: 2, yoyo: true }, 0.1
          );
        }

        // 3. Node shock & shake
        const partedNode = document.querySelector('.topo-node-g.node-parted');
        if (partedNode) {
          tl.fromTo(partedNode, 
            { x: '-=4' }, 
            { x: '+=8', duration: 0.06, repeat: 6, yoyo: true, ease: 'sine.inOut' }, 0.15
          );
          tl.fromTo('.node-shockwave',
            { scale: 0.8, opacity: 1 },
            { scale: 1.35, opacity: 0, duration: 0.8, repeat: 2, ease: 'power2.out' }, 0.2
          );
        }

        // 4. Probe tag & Broken link marker
        const probeTag = document.querySelector('.probe-tag');
        if (probeTag) {
          tl.fromTo(probeTag,
            { scale: 0, opacity: 0 },
            { scale: 1, opacity: 1, duration: 0.35, ease: 'back.out(2)' }, 0.15
          );
        }

        const brokenMarker = document.querySelector('.link-break-marker');
        if (brokenMarker) {
          tl.fromTo(brokenMarker,
            { scale: 0, opacity: 0 },
            { scale: 1, opacity: 1, duration: 0.4, ease: 'back.out(2)' }, 0.2
          );
        }
      }
    }

    function playAnimation() {
      if (activeTopoAnim.isPlaying) return;
      activeTopoAnim.isPlaying = true;
      if (btnPlay) {
        btnPlay.classList.add('playing');
        btnPlay.textContent = '⏸️ 暂停演示';
      }

      const intervalMs = Math.round(2800 / activeTopoAnim.speed);
      activeTopoAnim.timer = setInterval(() => {
        let nextIdx = activeTopoAnim.currentStepIndex + 1;
        if (nextIdx >= snapshots.length) {
          cleanUpTopologyAnimation();
          updateStepView(snapshots.length - 1);
          return;
        }
        updateStepView(nextIdx);
      }, intervalMs);
    }

    function pauseAnimation() {
      cleanUpTopologyAnimation();
      if (btnPlay) {
        btnPlay.classList.remove('playing');
        btnPlay.textContent = '▶️ 播放过程演示';
      }
    }

    if (btnPlay) {
      btnPlay.addEventListener('click', () => {
        if (activeTopoAnim.isPlaying) {
          pauseAnimation();
        } else {
          if (activeTopoAnim.currentStepIndex >= snapshots.length - 1) {
            activeTopoAnim.currentStepIndex = 0;
          }
          playAnimation();
        }
      });
    }

    if (btnPrev) {
      btnPrev.addEventListener('click', () => {
        pauseAnimation();
        const prevIdx = Math.max(0, activeTopoAnim.currentStepIndex - 1);
        updateStepView(prevIdx);
      });
    }

    if (btnNext) {
      btnNext.addEventListener('click', () => {
        pauseAnimation();
        const nextIdx = Math.min(snapshots.length - 1, activeTopoAnim.currentStepIndex + 1);
        updateStepView(nextIdx);
      });
    }

    if (btnReset) {
      btnReset.addEventListener('click', () => {
        pauseAnimation();
        updateStepView(0);
      });
    }

    speedBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        const spd = parseFloat(btn.dataset.speed) || 1;
        activeTopoAnim.speed = spd;
        speedBtns.forEach((b) => b.classList.toggle('active', b === btn));
        if (activeTopoAnim.isPlaying) {
          pauseAnimation();
          playAnimation();
        }
      });
    });

    pills.forEach((pill) => {
      pill.addEventListener('click', () => {
        pauseAnimation();
        const idx = parseInt(pill.dataset.stepIdx, 10);
        updateStepView(idx);
      });
    });

    // Node inspector selection
    const nodes = document.querySelectorAll('.topo-node-g');
    nodes.forEach((el) => {
      el.addEventListener('click', () => {
        nodes.forEach((n) => n.classList.remove('selected'));
        el.classList.add('selected');
        const nodeId = el.getAttribute('data-node-id');
        updateTopologyInspector(nodeId, topology);
      });
    });

    // Select first node by default
    if (nodes.length > 0) {
      nodes[0].classList.add('selected');
      updateTopologyInspector(nodes[0].getAttribute('data-node-id'), topology);
    }
  }

  // -------------------------------------------------------------------------
  // Topology Inspector HUD Panel
  // -------------------------------------------------------------------------
  function updateTopologyInspector(nodeId, topology) {
    const inspector = document.getElementById('topoInspector');
    if (!inspector || !topology) return;

    let targetNode = null;
    let targetCluster = null;

    (topology.clusters || []).forEach((cl) => {
      (cl.nodes || []).forEach((n) => {
        if (n.name === nodeId || n.label === nodeId) {
          targetNode = n;
          targetCluster = cl;
        }
      });
    });

    if (!targetNode && topology.clusters && topology.clusters[0] && topology.clusters[0].nodes[0]) {
      targetNode = topology.clusters[0].nodes[0];
      targetCluster = topology.clusters[0];
    }

    if (!targetNode) {
      inspector.innerHTML = '<div class="info-block">未选中节点</div>';
      return;
    }

    const role = (targetNode.role || 'STANDBY').toUpperCase();
    const isPrimary = role === 'PRIMARY';
    const isParted = role === 'PARTED' || role === 'OFFLINE' || targetNode.state === 'OFFLINE';

    const icon = isPrimary ? '👑' : (isParted ? '🚨' : '🔄');
    const roleText = isPrimary ? '集群主库 (PRIMARY)' : (isParted ? '已剔除/故障 (PARTED)' : '流复制备库 (STANDBY)');
    const stateColor = isParted ? '#f87171' : (isPrimary ? '#34d399' : '#38bdf8');

    inspector.innerHTML = `
      <div class="inspector-left">
        <span class="inspector-icon">${icon}</span>
        <div>
          <div class="inspector-title-text">${escapeHtml(targetNode.label || targetNode.name)} <small style="font-weight:normal; color:var(--text-muted);">(${escapeHtml(targetNode.name)})</small></div>
          <div class="inspector-sub-text">所属集群: <strong>${escapeHtml(targetCluster ? targetCluster.name : '-')}</strong> · 角色: <strong style="color:${stateColor}">${roleText}</strong></div>
        </div>
      </div>
      <div class="inspector-grid">
        <div class="inspector-item">
          <span class="ins-label">监听端口</span>
          <span class="ins-val">${escapeHtml(String(targetNode.port || '-'))}</span>
        </div>
        <div class="inspector-item">
          <span class="ins-label">宿主 IP</span>
          <span class="ins-val">${escapeHtml(targetNode.host || '127.0.0.1')}</span>
        </div>
        <div class="inspector-item">
          <span class="ins-label">路由属性</span>
          <span class="ins-val" style="color:${targetNode.is_write ? '#34d399' : '#38bdf8'}">${targetNode.is_write ? '可写路由 (Write Target)' : '只读副本 (Read Target)'}</span>
        </div>
        <div class="inspector-item">
          <span class="ins-label">探活状态</span>
          <span class="ins-val" style="color:${stateColor}">${escapeHtml(targetNode.state || 'ACTIVE')}</span>
        </div>
        <div class="inspector-item">
          <span class="ins-label">探活失败数</span>
          <span class="ins-val">${escapeHtml(String(targetNode.fault_count || '0'))}</span>
        </div>
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Topology Cards Fallback
  // -------------------------------------------------------------------------
  function renderTopologyCardsHtml(topology) {
    const clusters = topology.clusters || [];
    return clusters
      .map((cl) => {
        const isDegraded = cl.state && cl.state.includes('DEGRADED');
        const badgeClass = isDegraded ? 'degraded' : 'valid';
        const nodesHtml = (cl.nodes || [])
          .map((n) => {
            const role = (n.role || 'STANDBY').toUpperCase();
            let roleClass = 'standby';
            let roleIcon = '🔄';
            let roleName = '备节点 (Standby)';
            if (role === 'PRIMARY') {
              roleClass = 'primary';
              roleIcon = '👑';
              roleName = '主节点 (Primary)';
            } else if (role === 'PARTED' || role === 'OFFLINE' || n.state === 'OFFLINE') {
              roleClass = 'parted';
              roleIcon = '⚠️';
              roleName = '已隔离/切除 (Parted)';
            }

            const stateClass = (n.state || 'ACTIVE').toLowerCase() === 'active' ? 'active' : 'offline';
            return `
              <div class="node-card ${roleClass}">
                <div class="node-top">
                  <span class="node-label">${escapeHtml(n.label || n.name)} <small style="font-size:0.75rem; font-weight:normal; color:var(--text-muted);">(${escapeHtml(n.name)})</small></span>
                  <span class="node-role-badge ${roleClass}">${roleIcon} ${roleName}</span>
                </div>
                <div class="node-info-row">
                  <span>监听端口:</span>
                  <span class="node-info-val">${escapeHtml(String(n.port || '-'))}</span>
                </div>
                <div class="node-info-row">
                  <span>路由目标:</span>
                  <span class="node-info-val" style="color:${n.is_write ? '#34d399' : '#38bdf8'}">${n.is_write ? '可写目标 (Write)' : '只读副本 (Read)'}</span>
                </div>
                <div class="node-info-row">
                  <span>节点状态:</span>
                  <span class="node-state-pill ${stateClass}">${escapeHtml(n.state || 'ACTIVE')}</span>
                </div>
              </div>
            `;
          })
          .join('');

        return `
          <div class="cluster-card">
            <div class="cluster-header">
              <div class="cluster-title-group">
                <span class="cluster-name">${escapeHtml(cl.label || cl.name)}</span>
                <span class="cluster-badge ${badgeClass}">${escapeHtml(cl.state || 'VALID')}</span>
              </div>
              <span class="cluster-primary-tag">当前主节点: <strong>${escapeHtml(cl.primary || '-')}</strong></span>
            </div>
            <div class="nodes-grid">
              ${nodesHtml}
            </div>
          </div>
        `;
      })
      .join('');
  }



  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // =========================================================================
  // Multi-Mode Switcher (Deploy / Regress / Stable)
  // =========================================================================
  let currentActiveMode = 'deploy';
  let stablePollTimer = null;

  function initModeSwitcher() {
    const modeBtns = document.querySelectorAll('.nav-mode-btn');
    modeBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        const view = btn.getAttribute('data-view');
        switchViewMode(view);
      });
    });
  }

  function switchViewMode(view) {
    currentActiveMode = view;
    document.querySelectorAll('.nav-mode-btn').forEach((b) => {
      b.classList.toggle('active', b.getAttribute('data-view') === view);
    });

    const deployPanel = document.getElementById('viewDeploy');
    const regressPanel = document.getElementById('viewRegress');
    const stablePanel = document.getElementById('viewStable');

    if (deployPanel) deployPanel.style.display = view === 'deploy' ? 'flex' : 'none';
    if (regressPanel) regressPanel.style.display = view === 'regress' ? 'flex' : 'none';
    if (stablePanel) stablePanel.style.display = view === 'stable' ? 'flex' : 'none';

    if (view === 'deploy') {
      fetchEnvStatusAndRenderTopo();
    } else if (view === 'stable') {
      fetchStableStatusAndMetrics();
      if (!stablePollTimer) {
        stablePollTimer = setInterval(fetchStableStatusAndMetrics, 3000);
      }
    } else {
      if (stablePollTimer) {
        clearInterval(stablePollTimer);
        stablePollTimer = null;
      }
    }
  }

  // =========================================================================
  // Deploy & Topology Canvas Implementation
  // =========================================================================
  let topoDataCache = null;

  async function fetchEnvStatusAndRenderTopo() {
    try {
      const resp = await fetch('/api/env/status');
      const data = await resp.json();
      if (data.status === 'ok') {
        topoDataCache = data.env;
        renderTopologyCanvas(data.env);
        updateHealthStrip(data.env.health);
      }
    } catch (err) {
      console.error('Failed to fetch env status:', err);
    }
  }

  function updateHealthStrip(health) {
    if (!health) return;
    const mmr1Val = document.getElementById('healthMmr1Val');
    const mmr2Val = document.getElementById('healthMmr2Val');
    const proxyVal = document.getElementById('healthProxyVal');

    const mmr1Alive = health.mmr1?.total_alive || 0;
    const mmr2Alive = health.mmr2?.total_alive || 0;
    const totalAlive = mmr1Alive + mmr2Alive;

    if (mmr1Val) {
      mmr1Val.textContent = `${mmr1Alive} / 7 节点在线 (1 主 + 6 备)`;
      mmr1Val.style.color = mmr1Alive === 7 ? '#10b981' : (mmr1Alive > 0 ? '#f59e0b' : '#ef4444');
    }
    if (mmr2Val) {
      mmr2Val.textContent = `${mmr2Alive} / 7 节点在线 (1 主 + 6 备)`;
      mmr2Val.style.color = mmr2Alive === 7 ? '#10b981' : (mmr2Alive > 0 ? '#f59e0b' : '#ef4444');
    }
    if (proxyVal) {
      proxyVal.textContent = `${totalAlive} / 14 数据库节点在线 | 代理: ${health.proxy_running ? '在线就绪' : '已离线'}`;
      proxyVal.style.color = totalAlive === 14 ? '#10b981' : (totalAlive > 0 ? '#f59e0b' : '#ef4444');
    }
  }

  function renderTopologyCanvas(env) {
    const canvas = document.getElementById('topoCanvas');
    if (!canvas) return;

    const { nodes, edges, clusters, health = {} } = env;

    // Render Cluster Grouping Boxes with status pills
    let clusterBoxesHtml = '';
    if (clusters && Array.isArray(clusters)) {
      clusters.forEach((cl) => {
        const clClass = cl.type || 'mmr';
        const clHealth = health[cl.id];
        const alive = clHealth ? clHealth.total_alive : 0;
        const total = clHealth ? clHealth.total_count : 7;
        const isAllDown = alive === 0;

        let statusPill = '';
        if (isAllDown) {
          statusPill = `<span class="cluster-status-pill down">已离线 / 未部署 (0/${total})</span>`;
        } else if (alive === total) {
          statusPill = `<span class="cluster-status-pill active">全部就绪 (${alive}/${total})</span>`;
        } else {
          statusPill = `<span class="cluster-status-pill warn">部分在线 (${alive}/${total})</span>`;
        }

        const tagText = cl.type === 'mmr' ? `${cl.name} (1 主 + 6 物理从库)` : `${cl.name} (1 主 + 5 物理从库)`;
        clusterBoxesHtml += `
          <div class="cluster-group-box ${clClass} ${isAllDown ? 'cluster-down' : ''}" style="left: 60px; top: ${cl.y}px; width: 760px; height: ${cl.height}px;">
            <div class="cluster-group-tag ${isAllDown ? 'down' : ''}">${tagText} ${statusPill}</div>
          </div>
        `;
      });
    }

    // Build SVG Layer for animated / static inactive links
    let svgLines = '';
    edges.forEach((edge) => {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      const targetNode = nodes.find((n) => n.id === edge.target);
      if (!sourceNode || !targetNode) return;

      const sWidth = sourceNode.compact ? 195 : 220;
      const sHeight = sourceNode.compact ? 50 : 80;
      const tWidth = targetNode.compact ? 195 : 220;
      const tHeight = targetNode.compact ? 50 : 80;

      let x1, y1, x2, y2;

      if (edge.type === 'sync') {
        // Multi-master sync between mmr1 primary and mmr2 primary (vertical bidirectional link)
        x1 = sourceNode.x + 40;
        y1 = sourceNode.y + sHeight;
        x2 = targetNode.x + 40;
        y2 = targetNode.y;
      } else if (edge.type === 'replication') {
        // From primary right side to standby left side
        x1 = sourceNode.x + sWidth;
        y1 = sourceNode.y + 40;
        x2 = targetNode.x;
        y2 = targetNode.y + 25;
      } else {
        // Route from proxy to DB master
        x1 = sourceNode.x + sWidth;
        y1 = sourceNode.y + sHeight * 0.5;
        x2 = targetNode.x;
        y2 = targetNode.y + tHeight * 0.5;
      }

      // Curved bezier path
      const dx = Math.max(30, Math.abs(x2 - x1) * 0.5);
      const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

      const isEdgeActive = sourceNode.status === 'active' && targetNode.status === 'active' && Boolean(edge.animated);
      const edgeStatusClass = isEdgeActive ? 'edge-active' : 'edge-inactive';
      const edgeTypeClass = edge.type || 'route';
      svgLines += `
        <path d="${d}" class="topo-edge-line ${edgeTypeClass} ${edgeStatusClass}" />
      `;
    });

    const svgHtml = `
      <svg class="topo-svg-layer" width="1400" height="1020">
        <defs>
          <linearGradient id="edgeGlow" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.8"/>
            <stop offset="100%" stop-color="#2563eb" stop-opacity="0.3"/>
          </linearGradient>
        </defs>
        ${svgLines}
      </svg>
    `;

    // Build Node Hardware Cards
    let nodesHtml = '';
    nodes.forEach((n) => {
      const isDown = n.status !== 'active';
      const statusClass = isDown ? 'down' : 'active';
      const nodeCardClass = isDown ? 'node-down' : 'node-active';
      const roleBadge = isDown ? '离线 DOWN' : (n.role || n.type);
      const compactClass = n.compact ? 'compact' : '';
      const isDb = n.type === 'db_master' || n.type === 'db_standby' || n.type === 'proxy';

      // Clicking any database node directly opens the Web-PSQL Workbench!
      const clickAction = isDb
        ? `window.dashboard.openSqlWorkbench('${n.id}')`
        : `window.dashboard.inspectNode('${n.id}')`;

      const cardTitle = isDb
        ? `点击直接进入 Web-PSQL 交互控制台 (${escapeHtml(n.label || n.id)})，右上角 ⚙️ 可查看节点指标`
        : '点击查看详情';

      nodesHtml += `
        <div class="topo-node ${compactClass} ${nodeCardClass}" id="node_${n.id}" style="left: ${n.x}px; top: ${n.y}px;" onclick="${clickAction}" title="${cardTitle}">
          <div class="topo-node-header">
            <div class="topo-node-title">
              <span class="topo-node-pulse ${statusClass}"></span>
              <span class="node-label-text">${escapeHtml(n.label)}</span>
            </div>
            <div class="topo-node-badges">
              <span class="topo-node-badge ${statusClass}">${escapeHtml(roleBadge)}</span>
              ${isDb && !isDown ? `<button class="node-action-icon-btn zap" onclick="event.stopPropagation(); window.dashboard.showNodeQuickActions(event, '${n.id}')" title="快捷运维诊断指令">⚡</button>` : ''}
              ${isDb ? `<button class="node-action-icon-btn" onclick="event.stopPropagation(); window.dashboard.inspectNode('${n.id}')" title="查看节点运维与指标详情">⚙️</button>` : ''}
            </div>
          </div>
          <div class="topo-node-body">
            ${isDown ? `<div class="topo-node-field"><span class="field-key">状态:</span><span class="field-val status-field-down">● 已停止 (未启动)</span></div>` : ''}
            ${n.host ? `<div class="topo-node-field"><span class="field-key">Host:</span><span class="field-val">${escapeHtml(n.host)}</span></div>` : ''}
            ${n.port ? `<div class="topo-node-field"><span class="field-key">Port:</span><span class="field-val">${escapeHtml(n.port)}</span></div>` : ''}
            ${n.desc ? `<div class="topo-node-field"><span class="field-key">说明:</span><span class="field-val">${escapeHtml(n.desc)}</span></div>` : ''}
            ${isDb && !isDown ? `
              <div class="node-quick-btn-row">
                <button class="node-quick-zap-btn" onclick="event.stopPropagation(); window.dashboard.showNodeQuickActions(event, '${n.id}')" title="快速执行运维/排障指令">
                  <span>⚡ 快捷指令</span>
                </button>
                <div class="node-quick-sql-btn" onclick="event.stopPropagation(); window.dashboard.openSqlWorkbench('${n.id}')" title="进入 Web-PSQL 交互控制台">
                  <span>💻 SQL 控制台</span>
                </div>
              </div>
            ` : ''}
          </div>
        </div>
      `;
    });

    // Cleaned state banner overlay
    let bannerHtml = '';
    const totalActive = health.total_db_active || 0;
    const totalNodes = health.total_db_nodes || 14;

    if (totalActive === 0) {
      bannerHtml = `
        <div class="topo-canvas-state-banner down">
          <div class="banner-icon">🧹</div>
          <div class="banner-content">
            <div class="banner-title">集群当前处于【已清理 / 全部离线】状态 (0 / ${totalNodes} 节点在线)</div>
            <div class="banner-desc">所有数据库实例与流复制网络已停止。点击右上角【⚡ 一键部署 (Setup)】或【▶ 启动集群 (Start)】一键恢复集群环境。</div>
          </div>
          <button class="action-btn primary small" onclick="document.getElementById('btnDeploySetup').click()">⚡ 立即一键部署</button>
        </div>
      `;
    } else if (totalActive === totalNodes && health.proxy_running) {
      bannerHtml = `
        <div class="topo-canvas-state-banner active">
          <div class="banner-icon">✅</div>
          <div class="banner-content">
            <div class="banner-title">集群运行中：全部 ${totalNodes} 个数据库节点与代理网关正常就绪</div>
            <div class="banner-desc">MMR 多主双向数据同步与物理流复制链路处于活跃流动状态。</div>
          </div>
        </div>
      `;
    }

    canvas.innerHTML = bannerHtml + clusterBoxesHtml + svgHtml + nodesHtml;
  }

  // =========================================================================
  // Cluster Action Monitor & Execution Controller
  // =========================================================================
  let clusterPollTimer = null;
  let clusterLogOffset = 0;
  let isClusterActionRunning = false;
  let currentClusterAction = null;
  let currentClusterExtra = {};
  let clusterTotalLines = 0;

  const CLUSTER_ACTION_META = {
    setup: {
      title: '集群一键部署 (Setup)',
      icon: '⚡',
      runningText: '部署中...',
      isStepper: true,
    },
    clean: {
      title: '清理环境 (Clean)',
      icon: '🧹',
      runningText: '清理中...',
      isStepper: false,
    },
    start: {
      title: '启动集群 (Start)',
      icon: '▶',
      runningText: '启动中...',
      isStepper: false,
    },
    stop: {
      title: '停止集群 (Stop)',
      icon: '⏹',
      runningText: '停止中...',
      isStepper: false,
    },
    restart: {
      title: '重启集群 (Restart)',
      icon: '🔄',
      runningText: '重启中...',
      isStepper: false,
    },
    heal: {
      title: '故障自愈 (Heal)',
      icon: '🩺',
      runningText: '自愈中...',
      isStepper: false,
    },
    doctor: {
      title: '环境体检 (Doctor)',
      icon: '🔍',
      runningText: '体检中...',
      isStepper: false,
    },
  };

  const CLUSTER_BUTTON_MAP = {
    setup: 'btnDeploySetup',
    clean: 'btnDeployClean',
    start: 'btnDeployStart',
    stop: 'btnDeployStop',
    restart: 'btnDeployRestart',
    heal: 'btnDeployHeal',
    doctor: 'btnDeployDoctor',
  };

  const STEP_ORDER = [
    { key: 'preflight', name: '预检环境', pct: 15 },
    { key: 'clean_previous', name: '清理残留', pct: 30 },
    { key: 'setup_mmr_topology', name: '构建MMR', pct: 55 },
    { key: 'health_check', name: '健康检测', pct: 75 },
    { key: 'collect_test_context', name: '测试上下文', pct: 88 },
    { key: 'prepare_jdbc', name: '准备驱动', pct: 95 },
  ];

  function copyTextToClipboard(text, successMsg = '已复制到剪贴板') {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        alert(successMsg);
      }).catch(() => {
        fallbackCopy(text, successMsg);
      });
    } else {
      fallbackCopy(text, successMsg);
    }
  }

  function fallbackCopy(text, successMsg) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      alert(successMsg);
    } catch (e) {
      alert('复制失败，请手动选取复制');
    }
    document.body.removeChild(ta);
  }

  function formatDuration(sec) {
    const s = Math.floor(sec || 0);
    const m = Math.floor(s / 60);
    const remS = s % 60;
    return `${m.toString().padStart(2, '0')}:${remS.toString().padStart(2, '0')}`;
  }

  function formatTerminalLine(line) {
    const escaped = line
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    if (escaped.includes('[env] start') || escaped.includes('[env] done')) {
      return `<span class="log-line-env">${escaped}</span>`;
    }
    if (escaped.includes('[cmd]')) {
      return `<span class="log-line-cmd">${escaped}</span>`;
    }
    if (escaped.includes('=== STDOUT') || escaped.includes('=== STDERR') || escaped.includes('[Web Server]')) {
      return `<span class="log-line-head">${escaped}</span>`;
    }
    if (escaped.includes('[OK]') || escaped.includes('PASS') || escaped.includes('SUCCESS') || escaped.includes('complete')) {
      return `<span class="log-line-ok">${escaped}</span>`;
    }
    if (escaped.includes('WARN') || escaped.includes('WARNING')) {
      return `<span class="log-line-warn">${escaped}</span>`;
    }
    if (escaped.includes('ERROR') || escaped.includes('failed') || escaped.includes('FATAL') || escaped.includes('Traceback') || escaped.includes('Exception') || escaped.includes('ShellCommandError')) {
      return `<span class="log-line-err">${escaped}</span>`;
    }
    return escaped;
  }

  function lockClusterToolbar(activeBtnId, runningText) {
    const toolbar = document.getElementById('deployToolbar');
    if (toolbar) toolbar.classList.add('locked');
    const allIds = [
      'btnDeploySetup', 'btnDeployClean', 'btnDeployStart', 'btnDeployStop',
      'btnDeployRestart', 'btnDeployHeal', 'btnDeployDoctor', 'btnToggleWizard', 'btnRefreshTopo'
    ];
    allIds.forEach((id) => {
      const b = document.getElementById(id);
      if (b) b.disabled = true;
    });
    if (activeBtnId) {
      const activeBtn = document.getElementById(activeBtnId);
      if (activeBtn) {
        activeBtn.classList.add('btn-running');
        const textSpan = activeBtn.querySelector('.btn-text');
        if (textSpan) {
          activeBtn.dataset.origText = textSpan.textContent;
          textSpan.textContent = runningText || '执行中...';
        }
      }
    }
  }

  function unlockClusterToolbar() {
    const toolbar = document.getElementById('deployToolbar');
    if (toolbar) toolbar.classList.remove('locked');
    const allIds = [
      'btnDeploySetup', 'btnDeployClean', 'btnDeployStart', 'btnDeployStop',
      'btnDeployRestart', 'btnDeployHeal', 'btnDeployDoctor', 'btnToggleWizard', 'btnRefreshTopo'
    ];
    allIds.forEach((id) => {
      const b = document.getElementById(id);
      if (b) {
        b.disabled = false;
        b.classList.remove('btn-running');
        const textSpan = b.querySelector('.btn-text');
        if (textSpan && b.dataset.origText) {
          textSpan.textContent = b.dataset.origText;
        }
      }
    });
  }

  function updateStepperUI(currentStep, completedSteps = []) {
    const stepsRow = document.getElementById('envStepsRow');
    const progressFill = document.getElementById('envProgressFill');
    const stepDescText = document.getElementById('envStepDescText');
    const runningStepTag = document.getElementById('runningStepTag');
    if (!stepsRow || !progressFill) return;

    let targetPct = 10;
    const stepItems = stepsRow.querySelectorAll('.env-step-item');
    stepItems.forEach((item) => {
      const sKey = item.dataset.step;
      const isCompleted = completedSteps.includes(sKey);
      const isCurrent = currentStep === sKey;

      item.classList.remove('active', 'completed');
      if (isCompleted) {
        item.classList.add('completed');
      } else if (isCurrent) {
        item.classList.add('active');
      }
    });

    if (currentStep) {
      const matched = STEP_ORDER.find((s) => s.key === currentStep);
      if (matched) {
        targetPct = matched.pct;
        if (stepDescText) {
          stepDescText.textContent = `当前阶段: ${matched.name} (${currentStep})...`;
        }
        if (runningStepTag) {
          runningStepTag.textContent = `阶段: ${matched.name}`;
        }
      } else {
        if (stepDescText) stepDescText.textContent = `执行中: ${currentStep}...`;
        if (runningStepTag) runningStepTag.textContent = `阶段: ${currentStep}`;
      }
    }
    progressFill.style.width = `${targetPct}%`;
  }

  function openEnvActionModal() {
    const overlay = document.getElementById('envActionModalOverlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function closeEnvActionModal() {
    const overlay = document.getElementById('envActionModalOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  async function executeClusterAction(action, extra = {}) {
    if (isClusterActionRunning || isTaskRunning) {
      alert('当前已有任务正在执行中，请等待完成或点击终止。');
      openEnvActionModal();
      return;
    }

    const meta = CLUSTER_ACTION_META[action] || {
      title: `集群操作: ${action}`,
      icon: '⚡',
      runningText: '执行中...',
      isStepper: false,
    };

    currentClusterAction = action;
    currentClusterExtra = extra;
    isClusterActionRunning = true;
    clusterLogOffset = 0;
    clusterTotalLines = 0;

    const btnId = extra.btnId || CLUSTER_BUTTON_MAP[action];
    lockClusterToolbar(btnId, meta.runningText);

    // Setup UI components
    const deployRunningBar = document.getElementById('deployRunningBar');
    const runningActionText = document.getElementById('runningActionText');
    const runningStepTag = document.getElementById('runningStepTag');
    const runningTimer = document.getElementById('runningTimer');

    if (deployRunningBar) deployRunningBar.style.display = 'flex';
    if (runningActionText) runningActionText.textContent = meta.title;
    if (runningStepTag) runningStepTag.textContent = '步骤: 初始化...';
    if (runningTimer) runningTimer.textContent = '⏱ 00:00';

    // Reset Modal elements
    const envModalIcon = document.getElementById('envModalIcon');
    const envModalTitle = document.getElementById('envModalTitle');
    const envStatusBadge = document.getElementById('envStatusBadge');
    const envTimerBadge = document.getElementById('envTimerBadge');
    const envModalSubtitle = document.getElementById('envModalSubtitle');
    const envStepperBox = document.getElementById('envStepperBox');
    const envProgressFill = document.getElementById('envProgressFill');
    const envStepsRow = document.getElementById('envStepsRow');
    const envStepDescText = document.getElementById('envStepDescText');
    const envErrorBanner = document.getElementById('envErrorBanner');
    const envTerminalPre = document.getElementById('envTerminalPre');
    const envTerminalLineCount = document.getElementById('envTerminalLineCount');

    const btnModalAbortAction = document.getElementById('btnModalAbortAction');
    const btnModalMinimize = document.getElementById('btnModalMinimize');
    const btnModalRetry = document.getElementById('btnModalRetry');
    const btnModalDoneRefresh = document.getElementById('btnModalDoneRefresh');
    const btnModalCloseFailed = document.getElementById('btnModalCloseFailed');

    if (envModalIcon) envModalIcon.textContent = meta.icon;
    if (envModalTitle) envModalTitle.textContent = meta.title;
    if (envStatusBadge) {
      envStatusBadge.className = 'env-status-badge running';
      envStatusBadge.textContent = '执行中';
    }
    if (envTimerBadge) envTimerBadge.textContent = '⏱ 00:00';
    if (envModalSubtitle) envModalSubtitle.textContent = '正在执行集群底层运维指令，已锁定冲突操作以保证安全';
    if (envErrorBanner) envErrorBanner.style.display = 'none';
    if (envTerminalPre) envTerminalPre.innerHTML = '';
    if (envTerminalLineCount) envTerminalLineCount.textContent = '0 行';

    if (btnModalAbortAction) {
      btnModalAbortAction.style.display = 'inline-block';
      btnModalAbortAction.disabled = false;
      btnModalAbortAction.textContent = '⏹ 终止执行 (Abort)';
    }
    if (btnModalMinimize) btnModalMinimize.style.display = 'inline-block';
    if (btnModalRetry) btnModalRetry.style.display = 'none';
    if (btnModalDoneRefresh) btnModalDoneRefresh.style.display = 'none';
    if (btnModalCloseFailed) btnModalCloseFailed.style.display = 'none';

    // Toggle Stepper box view
    if (meta.isStepper) {
      if (envStepperBox) envStepperBox.style.display = 'block';
      if (envProgressFill) envProgressFill.style.width = '6%';
      if (envStepsRow) {
        envStepsRow.style.display = 'grid';
        const items = envStepsRow.querySelectorAll('.env-step-item');
        items.forEach((it, idx) => {
          it.classList.remove('active', 'completed');
          if (idx === 0) it.classList.add('active');
        });
      }
      if (envStepDescText) envStepDescText.textContent = '正在启动环境部署流程...';
    } else {
      if (envStepperBox) envStepperBox.style.display = 'block';
      if (envStepsRow) envStepsRow.style.display = 'none';
      if (envProgressFill) envProgressFill.style.width = '25%';
      if (envStepDescText) envStepDescText.textContent = `正在执行 ${meta.title}...`;
    }

    openEnvActionModal();

    try {
      const resp = await fetch('/api/env/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...extra }),
      });
      const res = await resp.json();
      if (res.status !== 'ok') {
        throw new Error(res.message || '启动操作失败');
      }

      startPollingClusterTask(res.target || `env.${action}`);
    } catch (err) {
      isClusterActionRunning = false;
      unlockClusterToolbar();
      if (envStatusBadge) {
        envStatusBadge.className = 'env-status-badge failed';
        envStatusBadge.textContent = '启动失败';
      }
      if (envErrorBanner) {
        envErrorBanner.style.display = 'block';
        const codeBadge = document.getElementById('envErrorCodeBadge');
        if (codeBadge) codeBadge.textContent = '请求异常';
        const sumBox = document.getElementById('envErrorSummaryBox');
        if (sumBox) sumBox.textContent = err.message;
      }
      if (btnModalAbortAction) btnModalAbortAction.style.display = 'none';
      if (btnModalRetry) btnModalRetry.style.display = 'inline-block';
      if (btnModalCloseFailed) btnModalCloseFailed.style.display = 'inline-block';
      if (deployRunningBar) deployRunningBar.style.display = 'none';
    }
  }

  function startPollingClusterTask(targetName) {
    if (clusterPollTimer) clearInterval(clusterPollTimer);
    clusterPollTimer = setInterval(pollClusterTaskStatus, 450);
  }

  async function pollClusterTaskStatus() {
    try {
      const resp = await fetch(`/api/task?offset=${clusterLogOffset}`);
      const data = await resp.json();
      if (data.status !== 'ok') return;

      const task = data.task;
      const formattedTime = formatDuration(task.elapsed);

      const envTimerBadge = document.getElementById('envTimerBadge');
      const runningTimer = document.getElementById('runningTimer');
      if (envTimerBadge) envTimerBadge.textContent = `⏱ ${formattedTime}`;
      if (runningTimer) runningTimer.textContent = `⏱ ${formattedTime}`;

      const envTerminalPre = document.getElementById('envTerminalPre');
      const envTerminalScroll = document.getElementById('envTerminalScroll');
      const chkEnvAutoScroll = document.getElementById('chkEnvAutoScroll');
      const envTerminalLineCount = document.getElementById('envTerminalLineCount');

      if (task.lines && task.lines.length > 0) {
        const formattedLines = task.lines.map(formatTerminalLine).join('\n') + '\n';
        if (envTerminalPre) {
          envTerminalPre.innerHTML += formattedLines;
        }
        clusterLogOffset = task.offset;
        clusterTotalLines += task.lines.length;
        if (envTerminalLineCount) {
          envTerminalLineCount.textContent = `${clusterTotalLines} 行`;
        }
        if (chkEnvAutoScroll && chkEnvAutoScroll.checked && envTerminalScroll) {
          envTerminalScroll.scrollTop = envTerminalScroll.scrollHeight;
        }

        // Intelligently parse and update sub-step description
        const envStepDescText = document.getElementById('envStepDescText');
        if (envStepDescText && task.lines && task.lines.length > 0) {
          for (let i = task.lines.length - 1; i >= 0; i--) {
            const line = task.lines[i];
            if (line.includes('pg_basebackup') || line.includes('waiting for checkpoint')) {
              envStepDescText.textContent = '正在通过 pg_basebackup 构建流复制从库数据文件...';
              break;
            } else if (line.includes('waiting for server to start')) {
              envStepDescText.textContent = '正在启动数据库服务进程并校验本地端口...';
              break;
            } else if (line.includes('all tables in subscription') || line.includes('node join to group')) {
              envStepDescText.textContent = '正在同步 MMR 双向多主集群数据与订阅状态...';
              break;
            } else if (line.includes('check_mmr_streaming')) {
              envStepDescText.textContent = '正在验证物理流复制连通性与从库状态...';
              break;
            } else if (line.includes('collect_remote_context')) {
              envStepDescText.textContent = '正在收集 MMR 集群认证凭证与 System ID 上下文...';
              break;
            } else if (line.includes('clean_previous') || line.includes('stop/remove')) {
              envStepDescText.textContent = '正在停止旧实例并清理残留数据目录...';
              break;
            } else if (line.includes('still running after')) {
              const secMatch = line.match(/still running after (\d+)s/);
              const s = secMatch ? secMatch[1] : '';
              envStepDescText.textContent = `底层脚本正在执行构建任务 (已运行 ${s}s，请稍候)...`;
              break;
            }
          }
        }
      }

      // Update stepper state
      if (task.current_step || task.completed_steps) {
        updateStepperUI(task.current_step, task.completed_steps || []);
      }

      if (!task.active && task.status !== 'running') {
        // Task completed!
        clearInterval(clusterPollTimer);
        clusterPollTimer = null;
        isClusterActionRunning = false;
        unlockClusterToolbar();

        const envStatusBadge = document.getElementById('envStatusBadge');
        const envProgressFill = document.getElementById('envProgressFill');
        const envStepDescText = document.getElementById('envStepDescText');
        const btnModalAbortAction = document.getElementById('btnModalAbortAction');
        const btnModalMinimize = document.getElementById('btnModalMinimize');
        const btnModalDoneRefresh = document.getElementById('btnModalDoneRefresh');
        const btnModalRetry = document.getElementById('btnModalRetry');
        const btnModalCloseFailed = document.getElementById('btnModalCloseFailed');
        const envErrorBanner = document.getElementById('envErrorBanner');
        const deployRunningBar = document.getElementById('deployRunningBar');

        if (btnModalAbortAction) btnModalAbortAction.style.display = 'none';
        if (btnModalMinimize) btnModalMinimize.style.display = 'none';

        if (task.status === 'success') {
          if (envStatusBadge) {
            envStatusBadge.className = 'env-status-badge success';
            envStatusBadge.textContent = '执行成功';
          }
          if (envProgressFill) envProgressFill.style.width = '100%';
          if (envStepDescText) envStepDescText.textContent = '✅ 操作已顺利完成，所有节点与状态就绪！';
          if (btnModalDoneRefresh) btnModalDoneRefresh.style.display = 'inline-block';

          // Mark all steps completed if stepper
          const stepsRow = document.getElementById('envStepsRow');
          if (stepsRow) {
            stepsRow.querySelectorAll('.env-step-item').forEach((it) => {
              it.classList.remove('active');
              it.classList.add('completed');
            });
          }

          if (deployRunningBar) {
            deployRunningBar.style.display = 'none';
          }

          // Auto refresh topology
          await fetchEnvStatusAndRenderTopo();
        } else if (task.status === 'failed') {
          if (envStatusBadge) {
            envStatusBadge.className = 'env-status-badge failed';
            envStatusBadge.textContent = '执行失败';
          }
          if (envStepDescText) envStepDescText.textContent = '❌ 操作执行遇到错误，请查看下方诊断卡片与控制台日志。';
          if (envErrorBanner) {
            envErrorBanner.style.display = 'block';
            const codeBadge = document.getElementById('envErrorCodeBadge');
            if (codeBadge) codeBadge.textContent = `退出码: ${task.exit_code || 1}`;
            const sumBox = document.getElementById('envErrorSummaryBox');
            if (sumBox) sumBox.textContent = task.error_summary || '执行返回非零状态，请排查详细日志。';
          }
          if (btnModalRetry) btnModalRetry.style.display = 'inline-block';
          if (btnModalCloseFailed) btnModalCloseFailed.style.display = 'inline-block';
          if (deployRunningBar) {
            const runningStepTag = document.getElementById('runningStepTag');
            if (runningStepTag) runningStepTag.textContent = '执行失败';
          }
        } else if (task.status === 'stopped') {
          if (envStatusBadge) {
            envStatusBadge.className = 'env-status-badge stopped';
            envStatusBadge.textContent = '已手动终止';
          }
          if (envStepDescText) envStepDescText.textContent = '⏹ 操作已被手动取消。';
          if (btnModalRetry) btnModalRetry.style.display = 'inline-block';
          if (btnModalCloseFailed) btnModalCloseFailed.style.display = 'inline-block';
          if (deployRunningBar) deployRunningBar.style.display = 'none';
        }
      }
    } catch (err) {
      console.error('Poll cluster task failed:', err);
    }
  }

  async function abortClusterAction() {
    if (!isClusterActionRunning) return;
    if (!confirm('确认终止当前正在执行的集群操作吗？\n终止可能导致集群处于中间状态，之后可通过【故障自愈】或【重新部署】恢复。')) return;

    const btnModalAbort = document.getElementById('btnModalAbortAction');
    const btnRibbonAbort = document.getElementById('btnAbortRunningAction');
    if (btnModalAbort) {
      btnModalAbort.disabled = true;
      btnModalAbort.textContent = '正在终止...';
    }
    if (btnRibbonAbort) {
      btnRibbonAbort.disabled = true;
      btnRibbonAbort.textContent = '终止中...';
    }

    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch (err) {
      console.error('Stop error:', err);
    }
  }

  function setupDeployActions() {
    const bindBtn = (id, action, extra = {}) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('click', () => {
        executeClusterAction(action, { ...extra, btnId: id });
      });
    };

    bindBtn('btnDeploySetup', 'setup', { adopt_existing: true });
    bindBtn('btnDeployClean', 'clean');
    bindBtn('btnDeployStart', 'start');
    bindBtn('btnDeployStop', 'stop');
    bindBtn('btnDeployRestart', 'restart');
    bindBtn('btnDeployHeal', 'heal');
    bindBtn('btnDeployDoctor', 'doctor');

    // Refresh Topo
    const btnRefreshTopo = document.getElementById('btnRefreshTopo');
    if (btnRefreshTopo) {
      btnRefreshTopo.addEventListener('click', fetchEnvStatusAndRenderTopo);
    }

    // Modal Events
    const btnEnvModalClose = document.getElementById('btnEnvModalClose');
    if (btnEnvModalClose) {
      btnEnvModalClose.addEventListener('click', () => {
        if (isClusterActionRunning) {
          if (!confirm('任务仍在执行中，最小化后可在顶部工具栏查看进度，确认最小化吗？')) return;
        }
        closeEnvActionModal();
      });
    }

    const btnModalMinimize = document.getElementById('btnModalMinimize');
    if (btnModalMinimize) {
      btnModalMinimize.addEventListener('click', closeEnvActionModal);
    }

    const btnOpenActionModal = document.getElementById('btnOpenActionModal');
    if (btnOpenActionModal) {
      btnOpenActionModal.addEventListener('click', openEnvActionModal);
    }

    const btnModalAbortAction = document.getElementById('btnModalAbortAction');
    if (btnModalAbortAction) {
      btnModalAbortAction.addEventListener('click', abortClusterAction);
    }

    const btnAbortRunningAction = document.getElementById('btnAbortRunningAction');
    if (btnAbortRunningAction) {
      btnAbortRunningAction.addEventListener('click', abortClusterAction);
    }

    const btnModalRetry = document.getElementById('btnModalRetry');
    if (btnModalRetry) {
      btnModalRetry.addEventListener('click', () => {
        if (currentClusterAction) {
          executeClusterAction(currentClusterAction, currentClusterExtra);
        }
      });
    }

    const btnModalDoneRefresh = document.getElementById('btnModalDoneRefresh');
    if (btnModalDoneRefresh) {
      btnModalDoneRefresh.addEventListener('click', () => {
        closeEnvActionModal();
        fetchEnvStatusAndRenderTopo();
      });
    }

    const btnModalCloseFailed = document.getElementById('btnModalCloseFailed');
    if (btnModalCloseFailed) {
      btnModalCloseFailed.addEventListener('click', closeEnvActionModal);
    }

    const btnCopyErrorLog = document.getElementById('btnCopyErrorLog');
    if (btnCopyErrorLog) {
      btnCopyErrorLog.addEventListener('click', () => {
        const sumBox = document.getElementById('envErrorSummaryBox');
        if (sumBox) copyTextToClipboard(sumBox.textContent, '错误摘要已复制到剪贴板！');
      });
    }

    const btnCopyEnvLog = document.getElementById('btnCopyEnvLog');
    if (btnCopyEnvLog) {
      btnCopyEnvLog.addEventListener('click', () => {
        const termPre = document.getElementById('envTerminalPre');
        if (termPre) copyTextToClipboard(termPre.innerText, '控制台完整日志已复制！');
      });
    }

    const btnClearEnvLog = document.getElementById('btnClearEnvLog');
    if (btnClearEnvLog) {
      btnClearEnvLog.addEventListener('click', () => {
        const termPre = document.getElementById('envTerminalPre');
        if (termPre) termPre.innerHTML = '';
        const countSpan = document.getElementById('envTerminalLineCount');
        if (countSpan) countSpan.textContent = '0 行';
      });
    }

    // Wizard Drawer Toggle
    const btnToggleWizard = document.getElementById('btnToggleWizard');
    const btnCloseWizard = document.getElementById('btnCloseWizard');
    const btnWizardCancel = document.getElementById('btnWizardCancel');
    const wizardDrawer = document.getElementById('wizardDrawer');

    if (btnToggleWizard && wizardDrawer) {
      btnToggleWizard.addEventListener('click', () => {
        wizardDrawer.classList.toggle('collapsed');
      });
    }
    if (btnCloseWizard && wizardDrawer) {
      btnCloseWizard.addEventListener('click', () => {
        wizardDrawer.classList.add('collapsed');
      });
    }
    if (btnWizardCancel && wizardDrawer) {
      btnWizardCancel.addEventListener('click', () => {
        wizardDrawer.classList.add('collapsed');
      });
    }

    // Wizard Apply Action
    const btnWizardApply = document.getElementById('btnWizardApply');
    if (btnWizardApply) {
      btnWizardApply.addEventListener('click', () => {
        if (wizardDrawer) wizardDrawer.classList.add('collapsed');
        executeClusterAction('setup', { adopt_existing: true, btnId: 'btnDeploySetup' });
      });
    }
  }

  // =========================================================================
  // Stable & Stress Testing Implementation
  // =========================================================================
  async function fetchStableStatusAndMetrics() {
    try {
      const [statusResp, metricsResp] = await Promise.all([
        fetch('/api/stable/status').then((r) => r.json()),
        fetch('/api/stable/metrics').then((r) => r.json()),
      ]);

      if (statusResp.status === 'ok') {
        updateStableStatus(statusResp.stable);
      }
      if (metricsResp.status === 'ok') {
        updateStableMetrics(metricsResp.metrics);
      }
    } catch (e) {
      console.error('Failed to fetch stable metrics:', e);
    }
  }

  function updateStableStatus(st) {
    if (!st) return;
    const pulse = document.getElementById('stablePulseDot');
    const title = document.getElementById('stableStatusTitle');
    const elapsed = document.getElementById('stableElapsedVal');
    const asan = document.getElementById('stableAsanVal');

    if (pulse) {
      pulse.className = `pulse-dot ${st.running ? 'running' : 'idle'}`;
    }
    if (title) {
      title.textContent = `常稳状态: ${st.running ? '正在压测 (' + (st.current_target || 'all') + ')' : '未启动 / 待命'}`;
    }
    if (elapsed) {
      elapsed.textContent = `${st.elapsed || 0}s`;
    }
    if (asan) {
      asan.textContent = `${st.asan_alerts || 0} 项`;
      asan.style.color = st.asan_alerts > 0 ? '#ef4444' : '#10b981';
    }
  }

  function updateStableMetrics(met) {
    if (!met) return;
    const pidVal = document.getElementById('stablePidVal');
    const rssVal = document.getElementById('metricRssVal');
    const cpuVal = document.getElementById('metricCpuVal');
    const fdVal = document.getElementById('metricFdVal');

    if (pidVal) pidVal.textContent = met.pid || '-';
    if (rssVal && met.current) rssVal.textContent = `${met.current.rss_mb} MB`;
    if (cpuVal && met.current) cpuVal.textContent = `${met.current.cpu_pct}%`;
    if (fdVal && met.current) fdVal.textContent = met.current.fds;

    if (met.history && met.history.length > 0) {
      renderRssChart(met.history);
    }
  }

  function renderRssChart(history) {
    const pathElem = document.getElementById('rssPath');
    const pathFillElem = document.getElementById('rssPathFill');
    if (!pathElem) return;

    const maxRss = Math.max(...history.map((h) => h.rss_mb), 10);
    const width = 500;
    const height = 120;
    const padding = 15;

    const points = history.map((item, idx) => {
      const x = padding + (idx / Math.max(history.length - 1, 1)) * (width - 2 * padding);
      const y = height - padding - (item.rss_mb / maxRss) * (height - 2 * padding);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    const d = `M ${points.join(' L ')}`;
    pathElem.setAttribute('d', d);

    if (pathFillElem && points.length > 0) {
      const firstX = points[0].split(',')[0];
      const lastX = points[points.length - 1].split(',')[0];
      const fillD = `${d} L ${lastX},${height} L ${firstX},${height} Z`;
      pathFillElem.setAttribute('d', fillD);
    }
  }

  function setupStableActions() {
    const bindStable = (btnId, action) => {
      const btn = document.getElementById(btnId);
      if (!btn) return;
      btn.addEventListener('click', async () => {
        const selectedTarget = document.querySelector('input[name="stable_target"]:checked')?.value || 'all';
        try {
          btn.disabled = true;
          const resp = await fetch('/api/stable/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, target: selectedTarget }),
          });
          const res = await resp.json();
          if (res.status === 'ok') {
            openTerminal();
            startTaskPolling(res.target || `stable.${action}`);
          } else {
            alert(res.message || '执行失败');
          }
        } catch (e) {
          alert('请求异常: ' + e);
        } finally {
          btn.disabled = false;
        }
      });
    };

    bindStable('btnStableStart', 'start');
    bindStable('btnStableStop', 'stop');
    bindStable('btnStableReload', 'reload');
    bindStable('btnStableRecover', 'recover');
    bindStable('btnStableArchive', 'archive');

    // View Report
    const btnViewReport = document.getElementById('btnViewStableReport');
    const reportBox = document.getElementById('stableReportContainer');
    const reportPre = document.getElementById('stableReportPre');
    const btnCloseReport = document.getElementById('btnCloseStableReport');

    if (btnViewReport && reportBox) {
      btnViewReport.addEventListener('click', async () => {
        reportBox.style.display = 'block';
        reportPre.textContent = '正在加载常稳报告...';
        try {
          const resp = await fetch('/api/stable/report');
          const data = await resp.json();
          if (data.status === 'ok') {
            reportPre.textContent = data.content;
          } else {
            reportPre.textContent = data.message || '暂无常稳报告内容。';
          }
        } catch (e) {
          reportPre.textContent = '加载报告异常: ' + e;
        }
      });
    }

    if (btnCloseReport && reportBox) {
      btnCloseReport.addEventListener('click', () => {
        reportBox.style.display = 'none';
      });
    }
  }

  // =========================================================================
  // Canvas Interactive Zoom & Smooth Pan Controls
  // =========================================================================
  let canvasScale = 1.0;
  let canvasPanX = 0;
  let canvasPanY = 0;
  let isCanvasPanning = false;
  let startCanvasPanX = 0;
  let startCanvasPanY = 0;

  function setupCanvasZoomAndPan() {
    const stage = document.querySelector('.topo-stage-container');
    const canvas = document.getElementById('topoCanvas');
    const scaleBadge = document.getElementById('canvasScaleBadge');
    const btnIn = document.getElementById('btnZoomIn');
    const btnOut = document.getElementById('btnZoomOut');
    const btnReset = document.getElementById('btnResetView');
    const btnFit = document.getElementById('btnFitView');

    if (!stage || !canvas) return;

    function applyTransform(notify = true) {
      canvas.style.transformOrigin = '0 0';
      canvas.style.transform = `translate(${canvasPanX}px, ${canvasPanY}px) scale(${canvasScale})`;
      if (scaleBadge) {
        scaleBadge.textContent = `${Math.round(canvasScale * 100)}%`;
        scaleBadge.style.opacity = '1';
      }
      if (notify && scaleBadge) {
        clearTimeout(scaleBadge._fadeTimer);
        scaleBadge._fadeTimer = setTimeout(() => {
          if (scaleBadge) scaleBadge.style.opacity = '0.75';
        }, 1800);
      }
    }

    if (btnIn) {
      btnIn.addEventListener('click', (e) => {
        e.stopPropagation();
        canvasScale = Math.min(2.5, Math.round((canvasScale + 0.15) * 100) / 100);
        applyTransform();
      });
    }

    if (btnOut) {
      btnOut.addEventListener('click', (e) => {
        e.stopPropagation();
        canvasScale = Math.max(0.4, Math.round((canvasScale - 0.15) * 100) / 100);
        applyTransform();
      });
    }

    if (btnReset) {
      btnReset.addEventListener('click', (e) => {
        e.stopPropagation();
        canvasScale = 1.0;
        canvasPanX = 0;
        canvasPanY = 0;
        applyTransform();
        showToast('画布视角已重置 (100%)', 'info');
      });
    }

    if (btnFit) {
      btnFit.addEventListener('click', (e) => {
        e.stopPropagation();
        const stageW = stage.clientWidth - 40;
        const fitScale = Math.min(1.0, Math.max(0.45, stageW / 1420));
        canvasScale = Math.round(fitScale * 100) / 100;
        canvasPanX = 10;
        canvasPanY = 10;
        applyTransform();
        showToast(`已自适应画布视口 (${Math.round(canvasScale * 100)}%)`, 'info');
      });
    }

    // Wheel zoom
    stage.addEventListener('wheel', (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.08 : 0.08;
        canvasScale = Math.min(2.5, Math.max(0.4, Math.round((canvasScale + delta) * 100) / 100));
        applyTransform();
      }
    }, { passive: false });

    // Drag to Pan
    stage.addEventListener('mousedown', (e) => {
      if (
        e.target.closest('.topo-node') ||
        e.target.closest('.canvas-controls') ||
        e.target.closest('.node-inspector-drawer') ||
        e.target.closest('.wizard-drawer')
      ) {
        return;
      }
      isCanvasPanning = true;
      startCanvasPanX = e.clientX - canvasPanX;
      startCanvasPanY = e.clientY - canvasPanY;
      canvas.classList.add('is-panning');
    });

    window.addEventListener('mousemove', (e) => {
      if (!isCanvasPanning) return;
      canvasPanX = e.clientX - startCanvasPanX;
      canvasPanY = e.clientY - startCanvasPanY;
      applyTransform(false);
    });

    window.addEventListener('mouseup', () => {
      if (isCanvasPanning) {
        isCanvasPanning = false;
        canvas.classList.remove('is-panning');
      }
    });

    // Clicking blank canvas background closes inspector
    stage.addEventListener('click', (e) => {
      if (
        e.target.closest('.topo-node') ||
        e.target.closest('.node-inspector-drawer') ||
        e.target.closest('.canvas-controls')
      ) {
        return;
      }
      const drawer = document.getElementById('nodeInspectorDrawer');
      if (drawer && !drawer.classList.contains('collapsed')) {
        drawer.classList.add('collapsed');
      }
    });
  }

  // =========================================================================
  // Dedicated Cyberpunk Node Inspector Slide-over Drawer
  // =========================================================================
  let currentNodeInspecting = null;

  function setupNodeInspector() {
    const drawer = document.getElementById('nodeInspectorDrawer');
    const btnClose = document.getElementById('btnCloseInspector');
    const btnCopyConn = document.getElementById('btnCopyPsqlConn');
    const btnRefreshProbe = document.getElementById('btnRefreshNodeProbe');
    const btnRestart = document.getElementById('btnNodeRestart');
    const btnStop = document.getElementById('btnNodeStop');
    const btnStart = document.getElementById('btnNodeStart');
    const btnLog = document.getElementById('btnNodeLog');
    const btnCloseLog = document.getElementById('btnCloseNodeLog');
    const inspLogBox = document.getElementById('inspLogBox');

    if (btnClose && drawer) {
      btnClose.addEventListener('click', () => {
        drawer.classList.add('collapsed');
        currentNodeInspecting = null;
      });
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && drawer && !drawer.classList.contains('collapsed')) {
        drawer.classList.add('collapsed');
        currentNodeInspecting = null;
      }
    });

    if (btnCopyConn) {
      btnCopyConn.addEventListener('click', () => {
        const snippet = document.getElementById('inspConnSnippet');
        if (!snippet) return;
        navigator.clipboard.writeText(snippet.textContent.trim()).then(() => {
          showToast('已复制 PSQL 直连命令到剪贴板！', 'success');
        }).catch(() => {
          showToast('复制失败，请手动选取文本', 'warning');
        });
      });
    }

    const btnOpenSql = document.getElementById('btnOpenSqlWorkbench');
    if (btnOpenSql) {
      btnOpenSql.addEventListener('click', () => {
        if (drawer) drawer.classList.add('collapsed');
        if (currentNodeInspecting) {
          openSqlWorkbench(currentNodeInspecting);
        } else {
          openSqlWorkbench(null);
        }
      });
    }

    if (btnRefreshProbe) {
      btnRefreshProbe.addEventListener('click', () => {
        if (currentNodeInspecting) {
          probeAndRenderNode(currentNodeInspecting);
        }
      });
    }

    if (btnCloseLog && inspLogBox) {
      btnCloseLog.addEventListener('click', () => {
        inspLogBox.style.display = 'none';
      });
    }

    async function triggerNodeAction(action, label) {
      if (!currentNodeInspecting) return;
      const node = currentNodeInspecting;
      showToast(`正在${label}节点 ${node.label || node.id}...`, 'info');

      try {
        const resp = await fetch('/api/node/action', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ node_id: node.id, action: action }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
          showToast(`节点 ${node.label || node.id} 已${label}！`, 'success');
          await probeAndRenderNode(node);
          await fetchEnvStatusAndRenderTopo();
        } else {
          showToast(`${label}失败: ${data.message || data.output || '未知错误'}`, 'error');
        }
      } catch (err) {
        showToast(`${label}网络异常: ${err}`, 'error');
      }
    }

    if (btnRestart) {
      btnRestart.addEventListener('click', () => triggerNodeAction('restart', '重启'));
    }
    if (btnStop) {
      btnStop.addEventListener('click', () => triggerNodeAction('stop', '停止'));
    }
    if (btnStart) {
      btnStart.addEventListener('click', () => triggerNodeAction('start', '启动'));
    }

    if (btnLog && inspLogBox) {
      btnLog.addEventListener('click', async () => {
        if (!currentNodeInspecting) return;
        const node = currentNodeInspecting;
        inspLogBox.style.display = 'block';
        const logPre = document.getElementById('inspLogPre');
        if (logPre) logPre.textContent = '正在拉取最新日志...';
        try {
          const resp = await fetch(`/api/node/log?node_id=${node.id}&lines=50`);
          const data = await resp.json();
          if (data.status === 'ok' && data.log && data.log.content) {
            if (logPre) logPre.textContent = data.log.content;
          } else {
            if (logPre) logPre.textContent = (data.log && data.log.message) || '暂无日志输出。';
          }
        } catch (e) {
          if (logPre) logPre.textContent = '读取日志异常: ' + e;
        }
      });
    }
  }

  async function probeAndRenderNode(node) {
    const inspLatency = document.getElementById('inspLatency');
    const inspConns = document.getElementById('inspConns');
    const inspDownstreams = document.getElementById('inspDownstreams');
    const inspRecovery = document.getElementById('inspRecovery');
    const inspCurrentLsn = document.getElementById('inspCurrentLsn');
    const inspReplayLsn = document.getElementById('inspReplayLsn');
    const inspPgVersion = document.getElementById('inspPgVersion');
    const inspStatusBadge = document.getElementById('inspStatusBadge');
    const inspRoleBadge = document.getElementById('inspRoleBadge');

    if (inspLatency) inspLatency.textContent = '探测中...';

    try {
      const resp = await fetch(
        `/api/node/probe?node_id=${node.id}&host=${node.host || '192.168.0.15'}&port=${node.port || 0}`
      );
      const data = await resp.json();
      if (data.status === 'ok' && data.probe) {
        const p = data.probe;
        if (inspLatency) inspLatency.textContent = `${p.latency_ms} ms`;
        if (inspConns) inspConns.textContent = p.active_conns;
        if (inspDownstreams) inspDownstreams.textContent = p.downstream_count;
        if (inspRecovery) inspRecovery.textContent = p.role_desc || (p.is_recovery ? 'Standby (只读从库)' : 'Master (主库)');
        if (inspCurrentLsn) inspCurrentLsn.textContent = p.current_lsn || '-';
        if (inspReplayLsn) inspReplayLsn.textContent = p.replay_lsn || (p.is_recovery ? '-' : 'N/A (主库)');
        if (inspPgVersion) inspPgVersion.textContent = p.version || 'PostgreSQL 15.3';

        if (inspStatusBadge) {
          if (p.online) {
            inspStatusBadge.className = 'inspector-status-badge';
            inspStatusBadge.textContent = '● 在线 ACTIVE';
          } else {
            inspStatusBadge.className = 'inspector-status-badge down';
            inspStatusBadge.textContent = '● 离线 DOWN';
          }
        }

        if (inspRoleBadge) {
          if (!p.is_recovery) {
            inspRoleBadge.className = 'inspector-role-badge master';
            inspRoleBadge.textContent = 'PRIMARY';
          } else {
            inspRoleBadge.className = 'inspector-role-badge';
            inspRoleBadge.textContent = 'STANDBY';
          }
        }
      }
    } catch (err) {
      if (inspLatency) inspLatency.textContent = '探测超时';
    }
  }

  function inspectNode(nodeId) {
    if (!topoDataCache) return;
    const node = topoDataCache.nodes.find((n) => n.id === nodeId);
    if (!node) return;

    currentNodeInspecting = node;
    const drawer = document.getElementById('nodeInspectorDrawer');
    if (!drawer) return;

    const isClientOrProxy = node.type === 'client' || node.type === 'proxy';

    const inspNodeTitle = document.getElementById('inspNodeTitle');
    const inspNodeSub = document.getElementById('inspNodeSub');
    const inspClusterTag = document.getElementById('inspClusterTag');
    const inspRoleBadge = document.getElementById('inspRoleBadge');
    const inspConnSnippet = document.getElementById('inspConnSnippet');
    const inspDataDir = document.getElementById('inspDataDir');

    if (inspNodeTitle) inspNodeTitle.textContent = node.label || node.id;
    if (inspNodeSub) inspNodeSub.textContent = `${node.host || '本地'}:${node.port || '-'}`;
    if (inspClusterTag) {
      if (node.cluster === 'mmr1') inspClusterTag.textContent = 'MMR1 (复用REP)';
      else if (node.cluster === 'mmr2') inspClusterTag.textContent = 'MMR2 集群';
      else inspClusterTag.textContent = '网关代理';
    }

    if (inspRoleBadge) {
      inspRoleBadge.textContent = node.role || 'NODE';
      inspRoleBadge.className = node.type === 'db_master' ? 'inspector-role-badge master' : 'inspector-role-badge';
    }

    if (inspConnSnippet) {
      if (node.port) {
        inspConnSnippet.textContent = `psql -h ${node.host || '192.168.0.15'} -p ${node.port} -U postgres`;
      } else {
        inspConnSnippet.textContent = `psql -h 127.0.0.1 -p 17432 -U postgres (通过代理)`;
      }
    }

    const dirMap = {
      mmr1_primary: 'test_mmr1',
      mmr1_sb_1: 'test_mmr1_s1',
      mmr1_sb_2: 'test_mmr1_s2',
      mmr1_sb_3: 'test_mmr1_s3',
      mmr1_sb_4: 'test_mmr1_s4',
      mmr1_sb_5: 'test_mmr1_s5',
      mmr1_sb_6: 'test_mmr1_s6',
      mmr2_primary: 'test_mmr2',
      mmr2_sb_1: 'test_mmr2_s1',
      mmr2_sb_2: 'test_mmr2_s2',
      mmr2_sb_3: 'test_mmr2_s3',
      mmr2_sb_4: 'test_mmr2_s4',
      mmr2_sb_5: 'test_mmr2_s5',
      mmr2_sb_6: 'test_mmr2_s6',
    };
    const dName = dirMap[node.id] || node.id;
    if (inspDataDir) {
      inspDataDir.textContent = `/home/postgres/fbasecman_regress_v2_mmr/${dName}`;
    }

    drawer.classList.remove('collapsed');

    // If client or proxy, hide database-specific details
    const actionSections = drawer.querySelectorAll('.insp-detail-section');
    if (isClientOrProxy) {
      actionSections.forEach((sec) => (sec.style.display = 'none'));
    } else {
      actionSections.forEach((sec) => (sec.style.display = 'block'));
      probeAndRenderNode(node);
    }
  }

  function locateCase(suiteId, caseId) {
    switchViewMode('regress');
    expandedSuites.add(suiteId);
    renderTree();

    setTimeout(() => {
      let elem = document.getElementById(`case_${caseId}`);
      if (!elem) {
        elem = document.querySelector(`[data-target="${suiteId}.${caseId}"]`);
      }
      if (elem) {
        elem.scrollIntoView({ behavior: 'smooth', block: 'center' });
        elem.classList.add('case-locate-pulse');
        setTimeout(() => elem.classList.remove('case-locate-pulse'), 2400);
        showToast(`已定位到用例: ${caseId}`, 'success');
      }
    }, 120);
  }

  // =========================================================================
  // Web-PSQL Interactive Workbench Logic
  // =========================================================================
  let currentSqlNode = null;
  let currentSqlResult = null;
  let currentSqlViewMode = 'grid';

  function openSqlWorkbench(nodeOrId, defaultDb = 'postgres', initialSql = null, autoExecute = false) {
    let node = null;
    if (typeof nodeOrId === 'string') {
      if (topoDataCache && topoDataCache.nodes) {
        node = topoDataCache.nodes.find((n) => n.id === nodeOrId);
      }
    } else if (nodeOrId && typeof nodeOrId === 'object') {
      node = nodeOrId;
    }

    // Default to mmr1_primary if no node specified or found
    if (!node) {
      if (topoDataCache && topoDataCache.nodes && topoDataCache.nodes.length > 0) {
        node = topoDataCache.nodes.find((n) => n.id === 'mmr1_primary') || topoDataCache.nodes[0];
      } else {
        node = {
          id: 'mmr1_primary',
          label: 'MMR1 主库 (mmr1_primary)',
          host: '192.168.0.15',
          port: 10011,
          role: 'PRIMARY',
          cluster: 'mmr1',
          type: 'db_master',
        };
      }
    }

    currentSqlNode = node;
    const modal = document.getElementById('sqlWorkbenchModal');
    if (!modal) return;

    // Automatically collapse inspector drawer if open to avoid visual obstruction
    const drawer = document.getElementById('nodeInspectorDrawer');
    if (drawer && !drawer.classList.contains('collapsed')) {
      drawer.classList.add('collapsed');
    }

    // Update Header Badges & Connection Info
    const badge = document.getElementById('sqlNodeBadge');
    const roleBadge = document.getElementById('sqlRoleBadge');
    const hostPort = document.getElementById('sqlConnHostPort');
    const connUser = document.getElementById('sqlConnUser');

    if (badge) badge.textContent = `${node.label || node.id} (${node.port || 10011})`;
    if (roleBadge) {
      const isMaster = node.type === 'db_master' || (node.role && node.role.toUpperCase() === 'PRIMARY');
      roleBadge.textContent = isMaster ? 'PRIMARY' : 'STANDBY';
      roleBadge.className = `sql-role-badge ${isMaster ? 'primary' : 'standby'}`;
    }
    if (hostPort) hostPort.textContent = `${node.host || '192.168.0.15'}:${node.port || 10011}`;
    if (connUser) connUser.textContent = 'postgres';

    // Fetch dynamic database list for this node
    fetchDatabasesForNode(node, defaultDb);

    // If editor is specified with initialSql, set it. Otherwise if empty set starter query.
    const editor = document.getElementById('sqlInputArea');
    if (initialSql) {
      if (editor) editor.value = initialSql;
    } else if (editor && !editor.value.trim()) {
      editor.value = 'SELECT application_name, client_addr, state, sync_state FROM pg_stat_replication;';
    }

    // Display modal
    modal.style.display = 'flex';
    if (editor) {
      setTimeout(() => {
        editor.focus();
        if (autoExecute) {
          executeSqlQuery();
        }
      }, 100);
    }
  }

  function closeQuickActionsPopover() {
    const popover = document.getElementById('nodeQuickActionsPopover');
    if (popover) popover.style.display = 'none';
  }

  function runSqlQuickAction(nodeId, sql, defaultDb = 'postgres') {
    closeQuickActionsPopover();
    openSqlWorkbench(nodeId, defaultDb, sql, true);
  }

  function showNodeQuickActions(event, nodeId) {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    const popover = document.getElementById('nodeQuickActionsPopover');
    const labelEl = document.getElementById('popoverNodeLabel');
    const listEl = document.getElementById('popoverActionsList');
    if (!popover || !listEl) return;

    let node = null;
    if (topoDataCache && topoDataCache.nodes) {
      node = topoDataCache.nodes.find((n) => n.id === nodeId);
    }
    if (!node) return;

    if (labelEl) {
      labelEl.textContent = `${node.label || node.id} · 快捷指令`;
    }

    const isProxy = node.type === 'proxy';
    let actionsHtml = '';

    if (isProxy) {
      const proxyCmds = [
        { title: '查看连接池统计', desc: 'SHOW POOLS;', sql: 'SHOW POOLS;' },
        { title: '查看后端集群', desc: 'SHOW CLUSTERS;', sql: 'SHOW CLUSTERS;' },
        { title: '查看后端节点路由', desc: 'SHOW NODES;', sql: 'SHOW NODES;' },
        { title: '查看读写分离组', desc: 'SHOW GROUPS;', sql: 'SHOW GROUPS;' },
        { title: '查看服务端长连接', desc: 'SHOW SERVERS;', sql: 'SHOW SERVERS;' },
        { title: '查看客户端连接', desc: 'SHOW CLIENTS;', sql: 'SHOW CLIENTS;' },
        { title: '查看错误统计', desc: 'SHOW ERRORS;', sql: 'SHOW ERRORS;' },
      ];

      actionsHtml += `<div class="quick-action-category">代理控制台命令 (Console)</div>`;
      proxyCmds.forEach((cmd) => {
        actionsHtml += `
          <div class="quick-action-item" onclick="window.dashboard.runSqlQuickAction('${node.id}', '${cmd.sql.replace(/'/g, "\\'")}', 'console')">
            <div class="quick-action-title"><span>⚡</span> ${escapeHtml(cmd.title)}</div>
            <div class="quick-action-sql">${escapeHtml(cmd.desc)}</div>
          </div>
        `;
      });
    } else {
      const dbDiagnostics = [
        {
          title: '流复制状态监控',
          desc: 'pg_stat_replication',
          sql: 'SELECT application_name, client_addr, state, sync_state FROM pg_stat_replication;',
        },
        {
          title: '主库/备库角色判定',
          desc: 'pg_is_in_recovery()',
          sql: 'SELECT pg_is_in_recovery();',
        },
        {
          title: '连接与并发分布',
          desc: 'pg_stat_activity 汇总',
          sql: 'SELECT count(*), state FROM pg_stat_activity GROUP BY state;',
        },
        {
          title: '数据库事务与吞吐',
          desc: 'pg_stat_database 指标',
          sql: 'SELECT datname, numbackends, xact_commit, xact_rollback FROM pg_stat_database WHERE datname=current_database();',
        },
        {
          title: '排查非空闲慢查询',
          desc: '活跃长事务排查',
          sql: "SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC LIMIT 5;",
        },
      ];

      actionsHtml += `<div class="quick-action-category">数据库运行诊断 (SQL)</div>`;
      dbDiagnostics.forEach((cmd) => {
        actionsHtml += `
          <div class="quick-action-item" onclick="window.dashboard.runSqlQuickAction('${node.id}', '${cmd.sql.replace(/'/g, "\\'")}', 'postgres')">
            <div class="quick-action-title"><span>📊</span> ${escapeHtml(cmd.title)}</div>
            <div class="quick-action-sql">${escapeHtml(cmd.desc)}</div>
          </div>
        `;
      });

      // Also add Proxy HA Control Commands targeting this node
      const proxyNode = (topoDataCache && topoDataCache.nodes) ? topoDataCache.nodes.find((n) => n.type === 'proxy') : null;
      if (proxyNode) {
        const nodeName = node.id;
        const haCmds = [
          { title: '查询此节点状态', desc: `SHOW NODE_STATUS ${nodeName};`, sql: `SHOW NODE_STATUS ${nodeName};` },
          { title: '切换为主写节点', desc: `SET NODE WRITE ${nodeName} IN GROUP mmr_group;`, sql: `SET NODE WRITE ${nodeName} IN GROUP mmr_group;` },
          { title: '隔离/踢出此节点', desc: `SET NODE PARTED ${nodeName};`, sql: `SET NODE PARTED ${nodeName};` },
          { title: '恢复/激活此节点', desc: `SET NODE ACTIVE ${nodeName};`, sql: `SET NODE ACTIVE ${nodeName};` },
        ];

        actionsHtml += `<div class="quick-action-category">代理高可用管控 (HA Console)</div>`;
        haCmds.forEach((cmd) => {
          actionsHtml += `
            <div class="quick-action-item" onclick="window.dashboard.runSqlQuickAction('${proxyNode.id}', '${cmd.sql.replace(/'/g, "\\'")}', 'console')">
              <div class="quick-action-title"><span>🛠️</span> ${escapeHtml(cmd.title)}</div>
              <div class="quick-action-sql">${escapeHtml(cmd.desc)}</div>
            </div>
          `;
        });
      }
    }

    listEl.innerHTML = actionsHtml;

    // Position popover near click
    const clientX = event ? (event.clientX || 200) : 200;
    const clientY = event ? (event.clientY || 200) : 200;

    const winW = window.innerWidth;
    const winH = window.innerHeight;

    let left = clientX + 10;
    let top = clientY + 10;

    if (left + 360 > winW) {
      left = Math.max(10, clientX - 360);
    }
    if (top + 380 > winH) {
      top = Math.max(10, winH - 390);
    }

    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.display = 'block';
  }

  async function fetchDatabasesForNode(node, defaultDb = 'postgres') {
    const dbSelect = document.getElementById('sqlSelectDb');
    if (!dbSelect) return;

    try {
      const host = node.host || '192.168.0.15';
      const port = node.port || 10011;
      const resp = await fetch(`/api/sql/databases?host=${host}&port=${port}&user=postgres`);
      const data = await resp.json();
      if (data.status === 'ok' && Array.isArray(data.databases) && data.databases.length > 0) {
        dbSelect.innerHTML = '';
        data.databases.forEach((db) => {
          const opt = document.createElement('option');
          opt.value = db;
          opt.textContent = db;
          if (db === defaultDb) opt.selected = true;
          dbSelect.appendChild(opt);
        });
      }
    } catch (err) {
      console.warn('Failed to fetch databases for node:', err);
    }
  }

  function setupSqlWorkbench() {
    const modal = document.getElementById('sqlWorkbenchModal');
    const btnClose = document.getElementById('btnCloseSqlWorkbench');
    const btnTopNav = document.getElementById('btnTopNavSql');
    const btnInspector = document.getElementById('btnOpenSqlWorkbench');
    const btnExecute = document.getElementById('btnExecuteSql');
    const btnClear = document.getElementById('btnClearSql');
    const editor = document.getElementById('sqlInputArea');
    const btnViewGrid = document.getElementById('btnSqlViewGrid');
    const btnViewRaw = document.getElementById('btnSqlViewRaw');
    const btnExportCsv = document.getElementById('btnExportSqlCsv');
    const btnCopyRaw = document.getElementById('btnCopySqlRaw');

    // Open handlers
    if (btnTopNav) {
      btnTopNav.addEventListener('click', () => openSqlWorkbench(null));
    }
    if (btnInspector) {
      btnInspector.addEventListener('click', () => {
        openSqlWorkbench(currentNodeInspecting || null);
      });
    }

    // Close handlers
    function closeModal() {
      if (modal) modal.style.display = 'none';
    }
    if (btnClose) {
      btnClose.addEventListener('click', closeModal);
    }
    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
      });
    }
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal && modal.style.display !== 'none') {
        closeModal();
      }
    });

    // Template pills
    const templatePills = document.querySelectorAll('.template-pill');
    templatePills.forEach((pill) => {
      pill.addEventListener('click', () => {
        const sql = pill.getAttribute('data-sql');
        if (sql && editor) {
          editor.value = sql;
          executeSqlQuery();
        }
      });
    });

    // Clear editor
    if (btnClear && editor) {
      btnClear.addEventListener('click', () => {
        editor.value = '';
        editor.focus();
      });
    }

    // Execute button
    if (btnExecute) {
      btnExecute.addEventListener('click', () => executeSqlQuery());
    }

    // Keyboard shortcut in editor: Ctrl+Enter or Cmd+Enter
    if (editor) {
      editor.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          executeSqlQuery();
        } else if (e.key === 'Tab') {
          e.preventDefault();
          const start = editor.selectionStart;
          const end = editor.selectionEnd;
          editor.value = editor.value.substring(0, start) + '  ' + editor.value.substring(end);
          editor.selectionStart = editor.selectionEnd = start + 2;
        }
      });
    }

    // View switcher (Grid vs Raw PSQL Text)
    if (btnViewGrid && btnViewRaw) {
      btnViewGrid.addEventListener('click', () => switchSqlView('grid'));
      btnViewRaw.addEventListener('click', () => switchSqlView('raw'));
    }

    // Export CSV
    if (btnExportCsv) {
      btnExportCsv.addEventListener('click', exportSqlResultCsv);
    }

    // Copy Raw Output
    if (btnCopyRaw) {
      btnCopyRaw.addEventListener('click', copySqlRawOutput);
    }
  }

  function switchSqlView(mode) {
    currentSqlViewMode = mode;
    const btnGrid = document.getElementById('btnSqlViewGrid');
    const btnRaw = document.getElementById('btnSqlViewRaw');
    const gridView = document.getElementById('sqlGridScroll');
    const rawView = document.getElementById('sqlRawScroll');

    if (mode === 'grid') {
      if (btnGrid) btnGrid.classList.add('active');
      if (btnRaw) btnRaw.classList.remove('active');
      if (currentSqlResult && currentSqlResult.columns && currentSqlResult.columns.length > 0) {
        if (gridView) gridView.style.display = 'block';
        if (rawView) rawView.style.display = 'none';
      }
    } else {
      if (btnRaw) btnRaw.classList.add('active');
      if (btnGrid) btnGrid.classList.remove('active');
      if (currentSqlResult) {
        if (gridView) gridView.style.display = 'none';
        if (rawView) rawView.style.display = 'block';
      }
    }
  }

  async function executeSqlQuery() {
    const editor = document.getElementById('sqlInputArea');
    if (!editor) return;
    const sql = editor.value.trim();
    if (!sql) {
      showToast('请输入要执行的 SQL 语句', 'warning');
      editor.focus();
      return;
    }

    const host = currentSqlNode ? (currentSqlNode.host || '192.168.0.15') : '192.168.0.15';
    const port = currentSqlNode ? (currentSqlNode.port || 10011) : 10011;
    const dbSelect = document.getElementById('sqlSelectDb');
    const db = dbSelect ? dbSelect.value : 'postgres';
    const chkLimit = document.getElementById('chkSqlLimit');
    const limit = (chkLimit && chkLimit.checked) ? 200 : 0;

    const btnExecute = document.getElementById('btnExecuteSql');
    const statusTag = document.getElementById('sqlStatusTag');
    const summaryText = document.getElementById('sqlSummaryText');
    const timerText = document.getElementById('sqlTimerText');
    const errorBox = document.getElementById('sqlErrorBox');
    const errorPre = document.getElementById('sqlErrorPre');
    const placeholder = document.getElementById('sqlPlaceholder');
    const gridView = document.getElementById('sqlGridScroll');
    const rawView = document.getElementById('sqlRawScroll');

    if (btnExecute) btnExecute.disabled = true;
    if (statusTag) {
      statusTag.className = 'sql-status-tag running';
      statusTag.textContent = '执行中...';
    }
    if (summaryText) summaryText.textContent = `向 ${host}:${port}/${db} 发送查询...`;
    if (timerText) timerText.textContent = '⏱ ...';
    if (errorBox) errorBox.style.display = 'none';
    if (placeholder) placeholder.style.display = 'none';

    const startTime = performance.now();

    try {
      const resp = await fetch('/api/sql/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host,
          port,
          db,
          user: 'postgres',
          sql,
          limit,
        }),
      });
      const data = await resp.json();
      const elapsed = Math.round(performance.now() - startTime);

      if (btnExecute) btnExecute.disabled = false;

      if (data.status === 'ok') {
        const resObj = {
          columns: data.columns || (data.result && data.result.columns) || [],
          rows: data.rows || (data.result && data.result.rows) || [],
          row_count: data.row_count !== undefined ? data.row_count : ((data.result && data.result.row_count) || 0),
          raw_text: data.raw_output || (data.result && data.result.raw_text) || '',
          execution_time_ms: data.elapsed_ms !== undefined ? data.elapsed_ms : ((data.result && data.result.execution_time_ms) || elapsed),
        };
        currentSqlResult = resObj;
        if (statusTag) {
          statusTag.className = 'sql-status-tag success';
          statusTag.textContent = '成功';
        }
        if (summaryText) {
          summaryText.textContent = `返回 ${resObj.row_count} 行记录`;
        }
        if (timerText) {
          timerText.textContent = `⏱ ${resObj.execution_time_ms} ms`;
        }

        renderSqlResult(resObj);
        switchSqlView(currentSqlViewMode);
      } else {
        currentSqlResult = null;
        if (statusTag) {
          statusTag.className = 'sql-status-tag error';
          statusTag.textContent = '错误';
        }
        if (summaryText) summaryText.textContent = 'SQL 执行失败';
        if (timerText) timerText.textContent = `⏱ ${elapsed} ms`;
        if (errorBox) {
          errorBox.style.display = 'block';
          if (errorPre) errorPre.textContent = data.error || data.message || '未知执行错误';
        }
        if (gridView) gridView.style.display = 'none';
        if (rawView) rawView.style.display = 'none';
      }
    } catch (err) {
      if (btnExecute) btnExecute.disabled = false;
      if (statusTag) {
        statusTag.className = 'sql-status-tag error';
        statusTag.textContent = '网络错误';
      }
      if (summaryText) summaryText.textContent = '无法连接到后端 Web 服务';
      if (errorBox) {
        errorBox.style.display = 'block';
        if (errorPre) errorPre.textContent = String(err);
      }
      if (gridView) gridView.style.display = 'none';
      if (rawView) rawView.style.display = 'none';
    }
  }

  function renderSqlResult(result) {
    const tableHead = document.getElementById('sqlTableHead');
    const tableBody = document.getElementById('sqlTableBody');
    const rawPre = document.getElementById('sqlRawPre');

    if (rawPre) {
      rawPre.textContent = result.raw_text || '(无终端文本输出)';
    }

    if (!tableHead || !tableBody) return;
    tableHead.innerHTML = '';
    tableBody.innerHTML = '';

    const cols = result.columns || [];
    const rows = result.rows || [];

    if (cols.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 2;
      td.textContent = result.raw_text || '命令已成功执行，无表格结果集返回。';
      td.style.padding = '1.5rem';
      td.style.color = '#38bdf8';
      tr.appendChild(td);
      tableBody.appendChild(tr);
      return;
    }

    // Render Table Header
    const trHead = document.createElement('tr');
    const thIdx = document.createElement('th');
    thIdx.className = 'row-num-th';
    thIdx.textContent = '#';
    trHead.appendChild(thIdx);

    cols.forEach((col) => {
      const th = document.createElement('th');
      th.textContent = col;
      trHead.appendChild(th);
    });
    tableHead.appendChild(trHead);

    // Render Table Rows
    rows.forEach((row, rIdx) => {
      const tr = document.createElement('tr');
      if (rIdx % 2 === 1) tr.className = 'odd';

      const tdIdx = document.createElement('td');
      tdIdx.className = 'row-num';
      tdIdx.textContent = rIdx + 1;
      tr.appendChild(tdIdx);

      row.forEach((val) => {
        const td = document.createElement('td');
        if (val === null || val === undefined || val === '') {
          td.textContent = val === '' ? '' : '(null)';
          if (val === null || val === undefined) td.className = 'null-val';
        } else {
          td.textContent = String(val);
        }
        tr.appendChild(td);
      });
      tableBody.appendChild(tr);
    });
  }

  function exportSqlResultCsv() {
    if (!currentSqlResult || !currentSqlResult.columns || currentSqlResult.columns.length === 0) {
      showToast('暂无表格查询结果可导出', 'warning');
      return;
    }

    const cols = currentSqlResult.columns;
    const rows = currentSqlResult.rows || [];

    function escapeCsvCell(c) {
      if (c === null || c === undefined) return '';
      const str = String(c);
      if (str.includes(',') || str.includes('"') || str.includes('\n')) {
        return `"${str.replace(/"/g, '""')}"`;
      }
      return str;
    }

    let csvContent = cols.map(escapeCsvCell).join(',') + '\n';
    rows.forEach((row) => {
      csvContent += row.map(escapeCsvCell).join(',') + '\n';
    });

    const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const nodeName = currentSqlNode ? (currentSqlNode.id || 'db') : 'db';
    link.setAttribute('href', url);
    link.setAttribute('download', `psql_result_${nodeName}_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    showToast('已成功导出 CSV 文件！', 'success');
  }

  function copySqlRawOutput() {
    const rawPre = document.getElementById('sqlRawPre');
    if (!rawPre || !rawPre.textContent.trim()) {
      showToast('暂无文本输出可复制', 'warning');
      return;
    }
    navigator.clipboard.writeText(rawPre.textContent).then(() => {
      showToast('已复制 PSQL 结果到剪贴板！', 'success');
    }).catch(() => {
      showToast('复制失败，请手动选取文本', 'warning');
    });
  }

  // Export to window for inline onclick handlers
  window.dashboard = {
    toggleSuite,
    runTarget,
    viewReport,
    applyFilter,
    openFailedCasesModal,
    openSuiteTopology,
    openGlobalTopologyModal,
    inspectNode,
    openSqlWorkbench,
    showNodeQuickActions,
    closeQuickActionsPopover,
    runSqlQuickAction,
    locateCase,
    showToast,
  };

  // Setup global dismiss listener for Quick Actions popover
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeQuickActionsPopover();
    }
  });
  document.addEventListener('click', (e) => {
    const pop = document.getElementById('nodeQuickActionsPopover');
    if (pop && pop.style.display !== 'none' && !pop.contains(e.target) && !e.target.closest('.node-quick-zap-btn') && !e.target.closest('.node-action-icon-btn.zap')) {
      closeQuickActionsPopover();
    }
  });

  // Start app when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
