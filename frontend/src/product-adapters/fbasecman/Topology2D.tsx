import { useEffect, useMemo, useRef } from 'react';
import gsap from 'gsap';
import DOMPurify from 'dompurify';
import { renderTopologyGraphHtml } from './referenceTopologyGraph';
import './referenceTopologyGraph.css';
import type { RegressionTopology } from './topologyModel';

export default function Topology2D({ topology, position, playing, speed, selected, onSelect, onSelectStep, onTogglePlay, onSpeed }: {
  topology: RegressionTopology;
  position: number;
  playing: boolean;
  speed: number;
  selected: string | null;
  onSelect: (name: string) => void;
  onSelectStep: (index: number) => void;
  onTogglePlay: () => void;
  onSpeed: (speed: number) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const html = useMemo(() => DOMPurify.sanitize(
    renderTopologyGraphHtml(topology, position, { isPlaying: playing, speed }),
    { USE_PROFILES: { html: true, svg: true, svgFilters: true } },
  ),
    [topology, position, playing, speed]);
  const clusters = topology.step_snapshots[position]?.clusters || topology.clusters;
  const node = clusters.flatMap((cluster) => cluster.nodes).find((item) => item.name === selected) || clusters[0]?.nodes[0];

  useEffect(() => {
    if (!root.current) return;
    const context = gsap.context(() => {
      const timeline = gsap.timeline();
      timeline.fromTo('.anim-step-banner', { opacity: 0, y: -8 },
        { opacity: 1, y: 0, duration: .3, ease: 'power2.out' });
      timeline.fromTo('.radar-tag', { opacity: 0, scale: .85, y: 4 },
        { opacity: 1, scale: 1, y: 0, duration: .3, stagger: .08, ease: 'back.out(1.7)' }, .05);
      const status = topology.step_snapshots[position]?.proxy_state?.status;
      if (status === 'DEGRADED') timeline.fromTo('.proxy-main-rect', { stroke: '#fff', strokeWidth: 4 },
        { stroke: '#ef4444', strokeWidth: 2.5, duration: .25, repeat: 3, yoyo: true }, .1);
      if (status === 'DEBOUNCING') timeline.fromTo('.proxy-main-rect', { stroke: '#fff', strokeWidth: 3.5 },
        { stroke: '#f59e0b', strokeWidth: 2.2, duration: .25, repeat: 2, yoyo: true }, .1);
      if (status === 'RECOVERED') timeline.fromTo('.proxy-main-rect', { stroke: '#6ee7b7', strokeWidth: 3.5 },
        { stroke: '#10b981', strokeWidth: 2.2, duration: .35, repeat: 2, yoyo: true }, .1);
      if (root.current?.querySelector('.topo-node-g.node-parted')) timeline.fromTo('.topo-node-g.node-parted',
        { x: -4 }, { x: 4, duration: .06, repeat: 6, yoyo: true }, .15);
    }, root);
    return () => context.revert();
  }, [position, topology]);

  function handleClick(event: React.MouseEvent<HTMLDivElement>) {
    const target = event.target as Element;
    if (target.closest('#btnTopoAnimPrev')) onSelectStep(Math.max(0, position - 1));
    else if (target.closest('#btnTopoAnimNext')) onSelectStep(Math.min(topology.step_snapshots.length - 1, position + 1));
    else if (target.closest('#btnTopoAnimReset')) onSelectStep(0);
    else if (target.closest('#btnTopoAnimPlay')) onTogglePlay();
    else {
      const speedButton = target.closest('[data-speed]');
      const stepButton = target.closest('[data-step-idx]');
      const nodeButton = target.closest('[data-node-id]');
      if (speedButton) onSpeed(Number(speedButton.getAttribute('data-speed')) || 1);
      else if (stepButton) onSelectStep(Number(stepButton.getAttribute('data-step-idx')));
      else if (nodeButton) onSelect(nodeButton.getAttribute('data-node-id') || '');
    }
  }

  return <div className="reference-topology-2d" ref={root} onClick={handleClick}>
    <div dangerouslySetInnerHTML={{ __html: html }} />
    {node && <div className="topology-inspector reference-topology-inspector">
      <div className="inspector-left"><span className="inspector-icon">{node.role === 'PRIMARY' ? '👑' : '🔄'}</span>
        <div><div className="inspector-title-text">{node.label || node.name} <small>({node.name})</small></div>
          <div className="inspector-sub-text">所属集群：{node.cluster} · 角色：{node.role}</div></div></div>
      <div className="inspector-grid"><div className="inspector-item"><span className="ins-label">监听端口</span><strong>{node.port}</strong></div>
        <div className="inspector-item"><span className="ins-label">宿主 IP</span><strong>{node.host}</strong></div>
        <div className="inspector-item"><span className="ins-label">探活状态</span><strong>{node.state}</strong></div></div>
    </div>}
  </div>;
}
