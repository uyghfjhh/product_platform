// Ported from fbasecman_regress_v2/tools/web/app.js; drawing remains source-faithful.

  export function renderDeploymentCanvas(env) {

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

        const tagText = cl.tag || (cl.type === 'mmr' ? `${cl.name} (1 主 + 6 物理从库)` : `${cl.name} (1 主 + 5 物理从库)`);
        const boxWidth = cl.width || 760;
        clusterBoxesHtml += `
          <div class="cluster-group-box ${clClass} ${isAllDown ? 'cluster-down' : ''}" style="left: 60px; top: ${cl.y}px; width: ${boxWidth}px; height: ${cl.height}px;">
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

      let x1, y1, x2, y2, d;

      if (edge.type === 'sync') {
        const yDiff = Math.abs(targetNode.y - sourceNode.y);
        if (yDiff > 280) {
          // Curved arch to the left around intermediate node
          x1 = sourceNode.x;
          y1 = sourceNode.y + sHeight * 0.5;
          x2 = targetNode.x;
          y2 = targetNode.y + tHeight * 0.5;
          const arcX = Math.min(x1, x2) - 45;
          d = `M ${x1} ${y1} C ${arcX} ${y1}, ${arcX} ${y2}, ${x2} ${y2}`;
        } else {
          // Vertical sync between adjacent MMR primaries
          x1 = sourceNode.x + 50;
          y1 = sourceNode.y + sHeight;
          x2 = targetNode.x + 50;
          y2 = targetNode.y;
          const dy = Math.max(30, Math.abs(y2 - y1) * 0.5);
          d = `M ${x1} ${y1} C ${x1} ${y1 + dy}, ${x2} ${y2 - dy}, ${x2} ${y2}`;
        }
      } else if (edge.type === 'replication') {
        // From primary right side to standby left side
        x1 = sourceNode.x + sWidth;
        y1 = sourceNode.y + sHeight * 0.5;
        x2 = targetNode.x;
        y2 = targetNode.y + tHeight * 0.5;
        const dx = Math.max(30, Math.abs(x2 - x1) * 0.5);
        d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
      } else {
        // Route from proxy or primary to subscriber
        x1 = sourceNode.x + sWidth;
        y1 = sourceNode.y + sHeight * 0.5;
        x2 = targetNode.x;
        y2 = targetNode.y + tHeight * 0.5;
        const dx = Math.max(30, Math.abs(x2 - x1) * 0.5);
        d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
      }

      const isEdgeActive = sourceNode.status === 'active' && targetNode.status === 'active' && Boolean(edge.animated);
      const edgeStatusClass = isEdgeActive ? 'edge-active' : 'edge-inactive';
      const edgeTypeClass = edge.type || 'route';
      svgLines += `
        <path d="${d}" class="topo-edge-line ${edgeTypeClass} ${edgeStatusClass}" />
      `;
    });

    const svgHtml = `
      <svg class="topo-svg-layer" width="1400" height="1200">
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
        <div class="topo-node ${compactClass} ${nodeCardClass}" id="node_${n.id}" data-node-id="${escapeHtml(n.id)}" style="left: ${n.x}px; top: ${n.y}px;" onclick="${clickAction}" title="${cardTitle}">
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

    return bannerHtml + clusterBoxesHtml + svgHtml + nodesHtml;
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

