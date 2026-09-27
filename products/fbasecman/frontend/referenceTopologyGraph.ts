import type { RegressionNode, RegressionSnapshot, RegressionTopology } from './topologyModel';
// Ported from fbasecman_regress_v2/tools/web/app.js (2D SVG renderer).
// React owns playback; this pure function owns the reference SVG layout.

  export function renderTopologyGraphHtml(topology: RegressionTopology, stepIndex = 0, renderState: { isPlaying: boolean; speed: number } = { isPlaying: false, speed: 1 }) {
    const snapshots = topology.step_snapshots || [];
    const hasSnapshots = snapshots.length > 0;
    const currentSnap: RegressionSnapshot = snapshots[stepIndex] || snapshots[0] || { title: '', step_num: 0, clusters: [] };

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

    const emptyNode: RegressionNode = { name: '', host: '', port: '', role: '', state: '', cluster: '' };
    const priA = (clA.nodes || []).find((n) => n.role === 'PRIMARY') || (clA.nodes || [])[0] || emptyNode;
    const stdA = (clA.nodes || []).find((n) => n.role !== 'PRIMARY') || emptyNode;

    const priB = (clB.nodes || []).find((n) => n.role === 'PRIMARY') || (clB.nodes || [])[0] || emptyNode;
    const stdB = (clB.nodes || []).find((n) => n.role !== 'PRIMARY') || emptyNode;

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
                <button class="anim-btn ${renderState.isPlaying ? 'playing' : 'primary'}" id="btnTopoAnimPlay" title="自动播放/暂停">
                  ${renderState.isPlaying ? '⏸️ 暂停演示' : '▶️ 播放过程演示'}
                </button>
                <button class="anim-btn" id="btnTopoAnimNext" title="下一步 (快捷键 →)">⏭️ 下一步</button>
                <button class="anim-btn" id="btnTopoAnimReset" title="重置到第一步">🔄 重头演示</button>
              </div>

              <div class="anim-speed-selector">
                <span class="speed-label">倍速:</span>
                <button class="speed-btn ${renderState.speed === 1 ? 'active' : ''}" data-speed="1">1x</button>
                <button class="speed-btn ${renderState.speed === 1.5 ? 'active' : ''}" data-speed="1.5">1.5x</button>
                <button class="speed-btn ${renderState.speed === 2 ? 'active' : ''}" data-speed="2">2x</button>
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

  function renderSvgNode(n: RegressionNode, x: number, y: number, type: string, currentSnap: RegressionSnapshot | null = null) {
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


  function escapeHtml(str: unknown) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
