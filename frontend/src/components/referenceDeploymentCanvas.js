// Shared deployment renderer; layout dimensions come from the topology model.

  export function renderDeploymentCanvas(env) {

    const { nodes, edges, clusters, health = {}, bounds = { width: 1400, height: 1200 } } = env;

    // Render Cluster Grouping Boxes with status pills
    let clusterBoxesHtml = '';
    if (clusters && Array.isArray(clusters)) {
      clusters.forEach((cl) => {
        const clClass = cl.type || 'mmr';
        const clHealth = health[cl.id];
        const alive = clHealth ? clHealth.total_alive : 0;
        const total = clHealth ? clHealth.total_count : 7;
        const isAllDown = health.probed === false ? false : alive === 0;

        let statusPill = '';
        if (health.probed === false) {
          statusPill = `<span class="cluster-status-pill unknown">状态探测中…</span>`;
        } else if (isAllDown) {
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

      const sWidth = sourceNode.width || (sourceNode.compact ? 195 : 220);
      const sHeight = sourceNode.height || (sourceNode.compact ? 50 : 80);
      const tWidth = targetNode.width || (targetNode.compact ? 195 : 220);
      const tHeight = targetNode.height || (targetNode.compact ? 50 : 80);

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
        <path id="edge_${escapeHtml(edge.id)}" data-source="${escapeHtml(edge.source)}" data-target="${escapeHtml(edge.target)}" d="${d}" class="topo-edge-line ${edgeTypeClass} ${edgeStatusClass}" />
      `;
    });

    const svgHtml = `
      <svg class="topo-svg-layer" width="${bounds.width}" height="${bounds.height}">
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
      const isDown = n.status === 'down';
      const isUnknown = n.status === 'unknown';
      const statusClass = isDown ? 'down' : (isUnknown ? 'unknown' : 'active');
      const isPrimary = n.type === 'db_master';
      const accentClass = isDown ? 'accent-down' : (isPrimary ? 'accent-primary' : 'accent-standby');
      const roleBadge = isDown ? '离线 DOWN' : (isUnknown ? '探测中' : (n.role || (isPrimary ? 'Primary (写)' : 'Standby (读)')));
      const roleColorClass = isPrimary ? 'role-primary' : 'role-standby';
      const endpoint = n.endpoint || (n.host && n.port ? `${n.host}:${n.port}` : (n.host || ''));

      const pluginsHtml = (n.corePlugins || []).map((p) => {
        const pName = typeof p === 'object' ? p.name : p;
        const pType = (typeof p === 'object' ? p.type : p).toLowerCase();
        return `<span class="plugin-pill plugin-${escapeHtml(pType)}" title="核心扩展: ${escapeHtml(pName)}">${escapeHtml(pName)}</span>`;
      }).join('');

      const cardTitle = `点击查看节点详情 (${escapeHtml(n.label || n.id)}) 与单节点运维`;

      // PostgreSQL 根守护进程 (postmaster/pgmaster) PID，重启后 PID 变化直观可见
      const pidText = n.pid != null ? String(n.pid) : '--';
      const pidTooltip = n.pid != null
        ? `PostgreSQL 根守护进程 (postmaster/pgmaster) PID: ${n.pid}`
        : '未运行 / 未获取到 postmaster PID';
      const pidClass = n.pid != null ? 'has-pid' : 'no-pid';

      nodesHtml += `
        <div class="topo-node ${accentClass} ${isDown ? 'node-down' : 'node-active'}" id="node_${escapeHtml(n.id)}" data-node-id="${escapeHtml(n.id)}" style="left: ${n.x}px; top: ${n.y}px;${n.width ? `width: ${n.width}px;` : ''}${n.height ? `height: ${n.height}px;` : ''}" data-action="inspect" title="${cardTitle}">
          <div class="topo-node-header">
            <div class="topo-node-title">
              <span class="topo-node-pulse ${statusClass}"></span>
              <span class="node-label-text" title="${escapeHtml(n.label)}">${escapeHtml(n.label)}</span>
            </div>
            <div class="topo-node-badges">
              <span class="topo-node-role-badge ${statusClass === 'down' ? 'down' : roleColorClass}">${escapeHtml(roleBadge)}</span>
              ${pluginsHtml}
            </div>
          </div>
          <div class="topo-node-subline">
            <span class="topo-node-endpoint" title="连接地址: ${escapeHtml(endpoint)}">${escapeHtml(endpoint)}</span>
            <div class="topo-node-meta">
              <span class="topo-node-pid ${pidClass}" title="${escapeHtml(pidTooltip)}">PID: ${escapeHtml(pidText)}</span>
              <span class="topo-node-status-text ${statusClass}">
                ● ${escapeHtml(n.statusText || (isDown ? '已停止' : '运行中'))}
              </span>
            </div>
          </div>
        </div>
      `;
    });

    // Cleaned state banner overlay
    let bannerHtml = '';
    const totalActive = health.total_db_active || 0;
    const totalNodes = health.total_db_nodes || 14;

    if (health.probed === false) {
      bannerHtml = `
        <div class="topo-canvas-state-banner unknown">
          <div class="banner-icon">📡</div>
          <div class="banner-content">
            <div class="banner-title">正在探测节点运行状态…</div>
            <div class="banner-desc">节点状态尚未返回或探测请求失败，画布将在下一轮探测后自动刷新；也可点击右上角【🔄 刷新拓扑】手动重试。</div>
          </div>
        </div>
      `;
    } else if (totalActive === 0) {
      bannerHtml = `
        <div class="topo-canvas-state-banner down">
          <div class="banner-icon">🧹</div>
          <div class="banner-content">
            <div class="banner-title">集群当前处于【已清理 / 全部离线】状态 (0 / ${totalNodes} 节点在线)</div>
            <div class="banner-desc">所有数据库实例与流复制网络已停止。点击右上角【⚡ 一键部署 (Setup)】或【▶ 启动集群 (Start)】一键恢复集群环境。</div>
          </div>
          <button class="action-btn primary small" data-action="deploy">⚡ 立即一键部署</button>
        </div>
      `;
    } else if (totalActive === totalNodes && health.proxy_running) {
      bannerHtml = `
        <div class="topo-canvas-state-banner active">
          <div class="banner-icon">✅</div>
          <div class="banner-content">
            <div class="banner-title">集群运行中：全部 ${totalNodes} 个数据库节点正常就绪</div>
            <div class="banner-desc">实例探测全部在线，复制链路处于活跃流动状态。</div>
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

