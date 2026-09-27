import { useEffect, useRef, useState } from 'react';
import type { RegressionTopology } from './topologyModel';
import { mountReferenceThreeScene } from './referenceThreeScene';
import './referenceTopologyGraph.css';

export default function ReferenceThreeTopology({ topology, onSelect }: {
  topology: RegressionTopology;
  onSelect?: (name: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const callback = useRef(onSelect);
  callback.current = onSelect;
  const [selected, setSelected] = useState('');

  useEffect(() => {
    if (!root.current) return;
    return mountReferenceThreeScene(root.current, topology, (name) => {
      setSelected(name);
      callback.current?.(name);
    });
  }, [topology]);

  const node = topology.clusters.flatMap((cluster) => cluster.nodes)
    .find((item) => item.name === selected || item.label === selected);
  return <div className="reference-three-scene">
    <div ref={root} />
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
