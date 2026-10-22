import { useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import gsap from 'gsap';
import { Descriptions, Drawer, Tag } from 'antd';
import type { TopologyData, TopologyEdgeLink, TopologyNode } from '../platform/topology';
import { renderDeploymentCanvas } from './referenceDeploymentCanvas';
import './referenceDeploymentCanvas.css';
import { mmrHint, type MmrStatus } from '../platform/mmr';
import { CORE_EXTENSIONS, extensionCssClass, formatExtensionLabel } from '../platform/extensions';

type Observed = Record<string, { running: boolean | null; message?: string; pid?: number | null }> | null;

function toReference(topology: TopologyData, observed: Observed, mmrStatus?: MmrStatus | null, mmrStale = false) {
  const groups = Array.from(new Set(topology.nodes.map((node) => node.group || 'cluster')));

  // 逻辑复制链路:source 所在组是发布端,target 所在组是订阅端
  const pubGroups = new Set<string>();
  const subGroups = new Set<string>();
  const logicalEdges = topology.edges.filter((edge) => edge.kind === 'logical');
  logicalEdges.forEach((edge) => {
    const sourceNode = topology.nodes.find((node) => node.id === edge.source);
    const targetNode = topology.nodes.find((node) => node.id === edge.target);
    if (sourceNode?.group) pubGroups.add(sourceNode.group);
    if (targetNode?.group) subGroups.add(targetNode.group);
  });

  let currentY = 70;
  const clusters: Array<{ id: string; name: string; tag: string; type: string; y: number; height: number; width: number }> = [];
  const nodePositions: Record<string, { x: number; y: number; compact: boolean }> = {};

  groups.forEach((group) => {
    const members = topology.nodes.filter((n) => (n.group || 'cluster') === group);
    const primaries = members.filter((n) => n.role === 'primary' || n.role?.includes('primary'));
    const nonPrimaries = members.filter((n) => !primaries.includes(n));

    // 自适应列数：当备库数量 >= 4 时采用 3 列排布，大幅压缩垂直层高（从 3 排降至 2 排，立省 100px+）
    const standbyCols = nonPrimaries.length >= 4 ? 3 : (nonPrimaries.length > 1 ? 2 : 1);
    const colWidth = 305;
    const rowGap = 82;
    const primaryRows = Math.max(1, primaries.length);
    const standbyRows = Math.max(1, Math.ceil(nonPrimaries.length / standbyCols));
    const rows = Math.max(primaryRows, standbyRows);
    const groupHeight = 42 + rows * rowGap + 18;
    const groupWidth = Math.max(720, 360 + standbyCols * colWidth + 24);

    let groupTitle = `${group.toUpperCase()} 复制组`;
    let groupTag = `${group.toUpperCase()} 复制组 (${primaries.length} 主 + ${nonPrimaries.length} 备)`;
    if (pubGroups.has(group)) {
      groupTitle += ' · 发布端';
      groupTag += ' · 发布端';
    }
    if (subGroups.has(group)) {
      groupTitle += ' · 订阅端';
      groupTag += ' · 订阅端';
    }
    if (group === 'mac') {
      groupTitle = 'MAC 等保合规集群';
      groupTag = `MAC 等保安全集群 (${primaries.length} 主 + ${nonPrimaries.length} 备/订阅)`;
    } else if (group.startsWith('test_mmr')) {
      groupTitle = `${group.toUpperCase()} 集群`;
      groupTag = `${group.toUpperCase()} (1 主 + ${nonPrimaries.length} 物理从库)`;
    } else if (group.startsWith('mmr')) {
      groupTitle = `MMR 节点组 ${group.toUpperCase()}`;
      groupTag = `MMR ${group.toUpperCase()} 节点组 (1 主 + ${nonPrimaries.length} 物理备库)`;
    }

    clusters.push({
      id: group,
      name: groupTitle,
      tag: groupTag,
      type: group.startsWith('mmr') || group.startsWith('test_mmr') ? 'mmr' : 'rep',
      y: currentY,
      height: groupHeight,
      width: groupWidth,
    });

    primaries.forEach((priNode, pIdx) => {
      nodePositions[priNode.id] = {
        x: 45,
        y: currentY + 38 + (rows - primaryRows) * rowGap / 2 + pIdx * rowGap,
        compact: false,
      };
    });

    nonPrimaries.forEach((stdNode, sIdx) => {
      const col = sIdx % standbyCols;
      const row = Math.floor(sIdx / standbyCols);
      nodePositions[stdNode.id] = {
        x: 355 + col * colWidth,
        y: currentY + 38 + row * rowGap,
        compact: true,
      };
    });

    currentY += groupHeight + (topology.kind === 'mmr' ? 52 : (topology.kind === 'logical' ? 42 : 24));
  });

  // 逻辑复制链路中端点节点的发布/订阅身份
  const pubNodeIds = new Set(logicalEdges.map((edge) => edge.source));
  const subNodeIds = new Set(logicalEdges.map((edge) => edge.target));

  const nodes = topology.nodes.map((node) => {
    const primary = node.role === 'primary' || node.role?.includes('primary');
    const pos = nodePositions[node.id] || { x: 100, y: 100, compact: false };
    const isMacSubscriber = node.role === 'logical_subscriber' || node.id === 'logical_subscriber';
    const probe = observed?.[node.id];
    const logicalRole = pubNodeIds.has(node.id) ? '发布' : (subNodeIds.has(node.id) ? '订阅' : '');

    const rawExtensions = node.extensions || [];
    const extSet = new Set(rawExtensions);
    if (node.group?.startsWith('mmr') || topology.kind === 'mmr') extSet.add('fdd_mmr');
    if (node.group === 'mac' || topology.kind === 'mac') extSet.add('fbase_mac');
    if (node.group?.startsWith('citus') || topology.kind === 'citus') extSet.add('citus');

    const corePlugins: Array<{ name: string; type: 'mmr' | 'mac' | 'citus' | 'ext'; cls?: string }> = [];
    if (extSet.has('fdd_mmr')) corePlugins.push({ name: node.extension_versions?.fdd_mmr ? `MMR ${node.extension_versions.fdd_mmr}` : 'MMR', type: 'mmr' });
    if (extSet.has('fbase_mac')) corePlugins.push({ name: 'MAC', type: 'mac' });
    if (extSet.has('citus')) corePlugins.push({ name: 'Citus', type: 'citus' });
    for (const ext of extSet) {
      if (CORE_EXTENSIONS.has(ext)) continue;
      corePlugins.push({
        name: formatExtensionLabel(ext, node.extension_versions?.[ext]),
        type: 'ext',
        cls: extensionCssClass(ext),
      });
    }

    return {
      id: node.id,
      label: node.label,
      type: primary ? 'db_master' : 'db_standby',
      cluster: node.group,
      role: primary
        ? (logicalRole ? `Primary (写·${logicalRole})` : 'Primary (写)')
        : (isMacSubscriber ? '订阅端 (只读)' : 'Standby (读)'),
      host: node.host,
      port: node.port,
      endpoint: `${node.host}:${node.port}`,
      data_dir: node.data_dir,
      corePlugins,
      serverVersion: node.server_version,
      pid: probe?.pid,
      status: probe?.running == null ? 'unknown' : (probe.running ? 'active' : 'down'),
      statusText: probe?.running == null ? '探测中' : (probe.running ? '运行中' : '已停止'),
      x: pos.x,
      y: pos.y,
      compact: pos.compact,
      width: 280,
      // 插件徽章独立成行（约 2 个 pill 一行），卡片按行数加高
      height: 68 + Math.ceil(corePlugins.length / 2) * 20,
    };
  });

  let logicalLane = 0;
  const edges = topology.edges.map((edge) => {
    const link = edge.link;
    const laneIndex = edge.kind === 'logical' ? logicalLane++ : 0;
    const tablesText = !link || link.tables === 'all'
      ? '全部表'
      : (Array.isArray(link.tables) ? link.tables.join(', ') : String(link.tables));
    const live = link?.live;
    const liveText = live?.connected
      ? (live.state === 'streaming' ? '逻辑复制中' : `追赶中(${live.state})`)
      : (live ? '未连接' : '');
    const lagBytes = live?.replay_lag_bytes ?? 0;
    const lagText = lagBytes > 0
      ? (lagBytes >= 1048576 ? ` · 延迟 ${(lagBytes / 1048576).toFixed(1)}MB`
        : lagBytes >= 1024 ? ` · 延迟 ${(lagBytes / 1024).toFixed(1)}KB` : ` · 延迟 ${lagBytes}B`)
      : '';
    const logicalWarn = !!live && (!live.connected || lagBytes > 1048576);
    const logicalTitle = link
      ? `发布 publication ${link.publication} @ ${link.pub_cluster} → 订阅 ${link.subscription} @ ${link.sub_cluster}`
        + ` · slot ${link.slot} · 库 ${link.database} → ${link.sub_database}`
        + ` · 表 ${tablesText}${link.via_partition_root ? ' · via_partition_root' : ''}`
        + (link.publish ? ` · publish ${link.publish}` : '')
        + (live ? ` · apply worker ${live.connected ? `已连接(${live.state}, senders=${live.senders})` : '未连接'}` : '')
      : undefined;
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: edge.kind === 'streaming' ? 'replication' : (edge.kind === 'mmr' ? 'sync' : (edge.kind === 'logical' ? 'logical' : 'route')),
      animated: true,
      mmrText: edge.kind==='mmr' && mmrStatus ? (mmrStale ? 'MMR 状态已过期' : mmrStatus.issue_count ? `MMR ⚠ ${mmrStatus.issue_count}项异常：${mmrHint(mmrStatus.members.find(m=>m.issues.length)?.issues[0].code || '')}` : mmrStatus.unknown ? 'MMR 状态未确认' : 'MMR 双活') : undefined,
      mmrError: edge.kind==='mmr' && !!mmrStatus && (mmrStale || mmrStatus.unknown || mmrStatus.issue_count>0),
      logicalText: edge.kind === 'logical' && link
        ? `${link.database} · ${tablesText}${liveText ? ` · ${liveText}` : ''}${lagText}`
        : undefined,
      logicalWarn,
      logicalLane: laneIndex,
      logicalName: link?.name,
      logicalTitle,
    };
  });

  const health = Object.fromEntries(groups.map((group) => {
    const members = topology.nodes.filter((node) => (node.group || 'cluster') === group);
    return [group, {
      total_alive: members.filter((node) => observed?.[node.id]?.running === true).length,
      total_count: members.length
    }];
  }));
  const totalAlive = nodes.filter((node) => node.status === 'active').length;

  return {
    nodes,
    edges,
    clusters,
    bounds: { width: Math.max(920, ...clusters.map((group) => group.width + 120)), height: currentY + 20 },
    health: {
      ...health,
      total_db_nodes: nodes.length,
      total_db_active: totalAlive,
      probed: observed != null,
      proxy_running: true,
    }
  };
}

export default function DeploymentCanvas({ topology, observed, onSelectNode, onOpenSql, onDeploy, mmrStatus, mmrStale, onOpenMmr }: {
  topology: TopologyData;
  observed: Observed;
  onSelectNode: (node: TopologyNode) => void;
  onOpenSql: (node: TopologyNode) => void;
  onDeploy: () => void;
  mmrStatus?: MmrStatus | null;
  mmrStale?: boolean;
  onOpenMmr?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1.0);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [logicalLink, setLogicalLink] = useState<{ link: TopologyEdgeLink; source: string; target: string } | null>(null);
  const panStartRef = useRef({ startX: 0, startY: 0, initialPanX: 0, initialPanY: 0 });

  const reference = useMemo(() => toReference(topology, observed, mmrStatus, mmrStale), [topology, observed, mmrStatus, mmrStale]);
  const { bounds } = reference;
  const html = useMemo(() => DOMPurify.sanitize(
    renderDeploymentCanvas(reference),
    { USE_PROFILES: { html: true, svg: true, svgFilters: true } },
  ), [reference]);

  useEffect(() => {
    if (!containerRef.current) return;
    const availableWidth = containerRef.current.clientWidth - 40;
    setScale(Math.min(1, Math.max(0.2, Math.floor(availableWidth / bounds.width * 100) / 100)));
    setPan({ x: 10, y: 10 });
  }, [topology.target, bounds.width]);

  // GSAP Choreographed Node & Link Animations on status/topology update
  useEffect(() => {
    if (!canvasRef.current) return;
    const ctx = gsap.context(() => {
      const tl = gsap.timeline();
      tl.fromTo('.cluster-group-box',
        { opacity: 0, scale: 0.98 },
        { opacity: 1, scale: 1, duration: 0.4, stagger: 0.08, ease: 'power2.out' },
        0,
      );
      tl.fromTo('.topo-node',
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.35, stagger: 0.03, ease: 'back.out(1.5)' },
        0.05,
      );
      tl.fromTo('.topo-edge-line.edge-active',
        { opacity: 0.2 },
        { opacity: 1, duration: 0.6, ease: 'power1.inOut' },
        0.2,
      );
    }, canvasRef);
    return () => ctx.revert();
  }, [html]);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    function handleMouseOver(e: MouseEvent) {
      const card = (e.target as Element).closest<HTMLElement>('.topo-node[data-node-id]');
      if (!card) return;
      const nodeId = card.dataset.nodeId;
      if (!nodeId) return;
      const edges = el?.querySelectorAll<SVGPathElement>(
        `.topo-edge-line[data-source="${nodeId}"], .topo-edge-line[data-target="${nodeId}"]`
      );
      edges?.forEach((edge) => edge.classList.add('edge-highlight'));
    }
    function handleMouseOut(e: MouseEvent) {
      const card = (e.target as Element).closest<HTMLElement>('.topo-node[data-node-id]');
      if (!card) return;
      const edges = el?.querySelectorAll<SVGPathElement>('.topo-edge-line.edge-highlight');
      edges?.forEach((edge) => edge.classList.remove('edge-highlight'));
    }
    el.addEventListener('mouseover', handleMouseOver);
    el.addEventListener('mouseout', handleMouseOut);
    return () => {
      el.removeEventListener('mouseover', handleMouseOver);
      el.removeEventListener('mouseout', handleMouseOut);
    };
  }, [html]);

  function handleClick(event: React.MouseEvent<HTMLDivElement>) {
    const target = event.target as Element;
    const actionEl = target.closest<HTMLElement>('[data-action]');
    const action = actionEl?.dataset.action;
    if (action === 'mmr') { onOpenMmr?.(); return; }
    if (action === 'deploy') { onDeploy(); return; }
    if (action === 'logical') {
      const linkName = actionEl?.dataset.link;
      const edge = topology.edges.find((item) => item.kind === 'logical' && item.link?.name === linkName);
      if (edge?.link) setLogicalLink({ link: edge.link, source: edge.source, target: edge.target });
      return;
    }
    const card = target.closest<HTMLElement>('[data-node-id]');
    const node = topology.nodes.find((item) => item.id === card?.dataset.nodeId);
    if (!node) return;
    if (action === 'sql') {
      onOpenSql(node);
    } else {
      onSelectNode(node);
    }
  }

  function handleZoomIn() {
    setScale((prev) => Math.min(2.2, Math.round((prev + 0.15) * 100) / 100));
  }

  function handleZoomOut() {
    setScale((prev) => Math.max(0.45, Math.round((prev - 0.15) * 100) / 100));
  }

  function handleResetView() {
    setScale(1.0);
    setPan({ x: 0, y: 0 });
  }

  function handleFitView() {
    if (!containerRef.current) return;
    const stageW = containerRef.current.clientWidth - 40;
    const fitScale = Math.min(1.0, Math.max(0.2, Math.floor((stageW / bounds.width) * 100) / 100));
    setScale(fitScale);
    setPan({ x: 10, y: 10 });
  }

  function handleMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    const target = e.target as Element;
    if (
      target.closest('.topo-node') ||
      target.closest('.canvas-controls') ||
      target.closest('button')
    ) {
      return;
    }
    setIsPanning(true);
    panStartRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      initialPanX: pan.x,
      initialPanY: pan.y,
    };
  }

  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!isPanning) return;
    const dx = e.clientX - panStartRef.current.startX;
    const dy = e.clientY - panStartRef.current.startY;
    setPan({
      x: panStartRef.current.initialPanX + dx,
      y: panStartRef.current.initialPanY + dy,
    });
  }

  function handleMouseUp() {
    setIsPanning(false);
  }

  return (
    <div
      className="reference-deploy-stage topo-stage-container"
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      style={{ cursor: isPanning ? 'grabbing' : 'default', position: 'relative', overflow: 'hidden',
        height: Math.max(360, bounds.height * scale + Math.max(0, pan.y) + 20) }}
    >
      {/* Floating Canvas Controls */}
      <div
        className="canvas-controls"
        style={{
          position: 'absolute',
          top: 14,
          right: 18,
          zIndex: 10,
          display: 'flex',
          gap: 6,
          background: 'rgba(15, 23, 42, 0.75)',
          padding: '4px 6px',
          borderRadius: 8,
          border: '1px solid rgba(56, 189, 248, 0.25)',
          backdropFilter: 'blur(8px)',
        }}
      >
        <button className="canvas-btn" onClick={handleZoomIn} title="放大画布" style={btnStyle}>➕</button>
        <button className="canvas-btn" onClick={handleZoomOut} title="缩小画布" style={btnStyle}>➖</button>
        <button className="canvas-btn" onClick={handleResetView} title="重置视角 (100%)" style={btnStyle}>⟲</button>
        <button className="canvas-btn" onClick={handleFitView} title="自适应画布" style={btnStyle}>⛶</button>
      </div>

      {/* Floating Scale Badge */}
      <div
        className="canvas-scale-badge"
        style={{
          position: 'absolute',
          bottom: 14,
          right: 18,
          zIndex: 10,
          background: 'rgba(15, 23, 42, 0.75)',
          color: '#38bdf8',
          fontSize: 12,
          fontWeight: 600,
          fontFamily: 'monospace',
          padding: '3px 9px',
          borderRadius: 6,
          border: '1px solid rgba(56, 189, 248, 0.25)',
          pointerEvents: 'none',
        }}
      >
        {Math.round(scale * 100)}%
      </div>

      <div className="reference-deploy-viewport" style={{ width: '100%', height: '100%', maxHeight: 'none', overflow: 'hidden' }}>
        <div
          ref={canvasRef}
          className="reference-deploy-canvas topo-canvas"
          onClick={handleClick}
          style={{
            width: bounds.width,
            height: bounds.height,
            transformOrigin: '0 0',
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
            transition: isPanning ? 'none' : 'transform 0.15s ease-out',
          }}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>

      <Drawer
        title={logicalLink ? `逻辑复制链路 · ${logicalLink.link.name}` : ''}
        open={!!logicalLink}
        onClose={() => setLogicalLink(null)}
        width={480}
      >
        {logicalLink && (
          <Descriptions column={1} bordered size="small" items={[
            { key: 'pub', label: '发布端', children: `${logicalLink.link.pub_cluster} (${logicalLink.source})` },
            { key: 'publication', label: 'Publication', children: logicalLink.link.publication },
            { key: 'database', label: '数据库', children: `${logicalLink.link.database} → ${logicalLink.link.sub_database}` },
            { key: 'tables', label: '发布表', children: logicalLink.link.tables === 'all' ? '全部表 (FOR ALL TABLES)' : (Array.isArray(logicalLink.link.tables) ? logicalLink.link.tables.join(', ') : String(logicalLink.link.tables)) },
            { key: 'via_root', label: 'via_partition_root', children: logicalLink.link.via_partition_root ? <Tag color="green">开启</Tag> : <Tag>关闭</Tag> },
            { key: 'publish', label: '发布操作', children: logicalLink.link.publish || 'insert,update,delete,truncate (默认)' },
            { key: 'sub', label: '订阅端', children: `${logicalLink.link.sub_cluster} (${logicalLink.target})` },
            { key: 'subscription', label: 'Subscription', children: logicalLink.link.subscription },
            { key: 'slot', label: '复制槽', children: logicalLink.link.slot },
            {
              key: 'live', label: '运行状态',
              children: logicalLink.link.live?.connected
                ? <Tag color="green">{`已连接 · ${logicalLink.link.live.state} · senders=${logicalLink.link.live.senders ?? '-'} · 延迟 ${formatBytes(logicalLink.link.live.replay_lag_bytes ?? 0)}`}</Tag>
                : <Tag color="orange">未连接 / 无探测数据</Tag>,
            },
          ]} />
        )}
      </Drawer>
    </div>
  );
}

function formatBytes(n: number) {
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)}MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${n}B`;
}

const btnStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.08)',
  border: '1px solid rgba(255, 255, 255, 0.15)',
  color: '#e2e8f0',
  borderRadius: 6,
  padding: '4px 8px',
  fontSize: 12,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};
