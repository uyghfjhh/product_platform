import { useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Controls, Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { Grid, Segmented } from 'antd';
import '@xyflow/react/dist/style.css';

import type { Event } from '../api';
import ProductSceneDetails from '../product-adapters/sceneDetails';

gsap.registerPlugin(useGSAP);

type Entity = {
  id: string;
  label: string;
  kind: string;
  group?: string | null;
  configured_role?: string | null;
  details?: Record<string, unknown>;
};
type Relation = { id: string; source: string; target: string; kind: string };
type Observation = {
  entity_id: string;
  state: string;
  source: string;
  observed_at: string;
  details?: Record<string, unknown>;
};
type SceneNodeData = Record<string, unknown> & {
  entity: Entity;
  observation?: Observation;
  action?: string;
};
type SceneFlowNode = Node<SceneNodeData, 'scene'>;

function EntityNode({ data }: NodeProps<SceneFlowNode>) {
  const ref = useRef<HTMLDivElement>(null);
  const state = data.observation?.state || 'unknown';

  useGSAP(() => {
    if (!ref.current) return;
    if (data.action) {
      gsap.to(ref.current, {
        scale: 1.035, duration: 0.85, repeat: -1, yoyo: true,
        ease: 'sine.inOut', overwrite: true,
      });
      return;
    }
    if (!data.observation) return;
    gsap.fromTo(ref.current, { scale: 1.05 }, {
      scale: 1, duration: 0.48, ease: 'power2.out', overwrite: true,
    });
  }, { scope: ref, dependencies: [state, data.observation?.observed_at, data.action], revertOnUpdate: true });

  return (
    <div ref={ref} className={`scene-node scene-state-${state}${data.action ? ' scene-node-active' : ''}`}>
      <Handle type="target" position={Position.Left} className="scene-handle" />
      <div className="scene-node-top">
        <strong>{data.entity.label}</strong>
        <span className="scene-node-state">{data.observation ? state : '未观测'}</span>
      </div>
      <div className="scene-node-sub">
        {data.entity.configured_role && <span>配置角色：{data.entity.configured_role}</span>}
        {Boolean(data.entity.details?.host) && <span>{String(data.entity.details?.host)}:{String(data.entity.details?.port || '')}</span>}
        {data.observation?.details && <ProductSceneDetails kind={data.entity.kind} details={data.observation.details} />}
      </div>
      {data.action && <span className="scene-node-action">操作中：{data.action}</span>}
      {data.observation && <small>来源：{data.observation.source}</small>}
      <Handle type="source" position={Position.Right} className="scene-handle" />
    </div>
  );
}

const nodeTypes = { scene: EntityNode };

function sceneAt(events: Event[], position: number) {
  let entities: Entity[] = [];
  let relations: Relation[] = [];
  let product = '';
  const observations: Record<string, Observation> = {};
  const actions: Record<string, string> = {};
  for (const event of events.slice(0, position + 1)) {
    if (event.event_type === 'scene.topology.configured') {
      entities = Array.isArray(event.payload.entities) ? event.payload.entities as Entity[] : [];
      relations = Array.isArray(event.payload.relations) ? event.payload.relations as Relation[] : [];
      product = String(event.payload.product_id || '');
      for (const key of Object.keys(observations)) delete observations[key];
      for (const key of Object.keys(actions)) delete actions[key];
    } else if (event.event_type === 'scene.entity.discovered') {
      const value = event.payload as Entity;
      if (value.id && !entities.some((entity) => entity.id === value.id)) {
        entities = [...entities, value];
      }
    } else if (event.event_type === 'scene.entity.observed') {
      const value = event.payload as Observation;
      if (value.entity_id) {
        const previous = observations[value.entity_id];
        observations[value.entity_id] = previous ? {
          ...previous, ...value,
          details: { ...(previous.details || {}), ...(value.details || {}) },
        } : value;
      }
    } else if (event.event_type === 'scene.action.started') {
      const id = String(event.payload.entity_id || '');
      if (id) actions[id] = String(event.payload.action || '执行中');
    } else if (event.event_type === 'scene.action.finished') {
      delete actions[String(event.payload.entity_id || '')];
    }
  }
  return { entities, relations, observations, actions, product };
}

export default function SceneReplay({ events, position }: { events: Event[]; position: number }) {
  const screens = Grid.useBreakpoint();
  const compact = !screens.sm;
  const [mode, setMode] = useState<'observed' | 'configured' | 'all'>('observed');
  const scene = useMemo(() => sceneAt(events, position), [events, position]);
  const hasObservations = Object.keys(scene.observations).length > 0;
  const visibleEntities = useMemo(() => {
    if (!hasObservations || mode === 'all') return scene.entities;
    if (mode === 'configured') {
      return scene.entities.filter((entity) => entity.kind === 'endpoint' || entity.kind === 'database');
    }
    return scene.entities.filter((entity) => entity.kind === 'endpoint' || Boolean(scene.observations[entity.id]));
  }, [scene, hasObservations, mode]);
  const nodes = useMemo<SceneFlowNode[]>(() => visibleEntities.map((entity, index) => ({
    id: entity.id,
    type: 'scene',
    position: compact
      ? { x: 0, y: index * 135 }
      : entity.kind === 'endpoint'
        ? { x: 320, y: 0 }
        : { x: (index - 1) % 4 * 230, y: 145 + Math.floor((index - 1) / 4) * 125 },
    data: { entity, observation: scene.observations[entity.id], action: scene.actions[entity.id] },
    draggable: false,
  })), [visibleEntities, scene.observations, scene.actions, compact]);
  const edges = useMemo<Edge[]>(() => scene.relations.filter((relation) =>
    visibleEntities.some((entity) => entity.id === relation.source) &&
    visibleEntities.some((entity) => entity.id === relation.target)).map((relation) => ({
    id: relation.id, source: relation.source, target: relation.target,
    type: 'smoothstep', label: relation.kind,
    style: { strokeWidth: 1.5, stroke: '#83988b' },
  })), [scene.relations, visibleEntities]);

  if (!scene.entities.length) {
    return <div className="scene-empty">该任务没有结构化产品状态；仍可查看步骤与原始日志。</div>;
  }
  return (
    <div className="scene-replay">
      <div className="scene-caption">
        <strong>{scene.product || '产品'}状态</strong>
        {hasObservations && <Segmented
          size="small" value={mode} onChange={(value) => setMode(value as typeof mode)}
          options={[{ label: '实测', value: 'observed' }, { label: '配置', value: 'configured' }, { label: '全部', value: 'all' }]}
          aria-label="场景视图"
        />}
      </div>
      <div className="scene-canvas" style={compact ? {
        height: Math.min(1000, Math.max(430, visibleEntities.length * 125)),
      } : undefined}>
        <ReactFlow key={`${mode}:${compact}:${visibleEntities.map((entity) => entity.id).join('|')}`}
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView
          nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
          proOptions={{ hideAttribution: true }}>
          {!compact && <Controls showInteractive={false} />}
          <Background gap={20} color="#89988e" />
        </ReactFlow>
      </div>
    </div>
  );
}
