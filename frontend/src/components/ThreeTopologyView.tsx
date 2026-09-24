import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { ReloadOutlined, CompassOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons';

// Declare global THREE for TypeScript
declare global {
  interface Window {
    THREE?: any;
  }
}

export type TopologyNode = {
  id: string;
  label: string;
  host: string;
  port: number;
  role: string;
  group?: string;
  data_dir: string;
};

export type TopologyData = {
  target: string;
  kind: string;
  nodes: TopologyNode[];
  edges: { id: string; source: string; target: string; kind: string }[];
};

type Props = {
  topology: TopologyData;
  observed: Record<string, { running: boolean | null; message: string }> | null;
  onSelectNode: (node: TopologyNode) => void;
  height?: number | string;
};

export default function ThreeTopologyView({ topology, observed, onSelectNode, height = 560 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isAutoRotate, setIsAutoRotate] = useState(true);
  const controlsRef = useRef<any>(null);
  const cameraRef = useRef<any>(null);
  const activeContextRef = useRef<{ cleanup: () => void } | null>(null);

  useEffect(() => {
    const THREE = window.THREE;
    if (!THREE || !containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth || 900;
    const canvasHeight = typeof height === 'number' ? height : 560;

    // 1. Scene & Camera
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x060913, 0.012);

    const camera = new THREE.PerspectiveCamera(45, width / canvasHeight, 0.1, 1000);
    camera.position.set(0, 26, 44);
    cameraRef.current = camera;

    // 2. Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, canvasHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    container.innerHTML = '';
    container.appendChild(renderer.domElement);

    // 3. OrbitControls
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.maxPolarAngle = Math.PI / 2 + 0.05;
    controls.minDistance = 10;
    controls.maxDistance = 110;
    controls.autoRotate = isAutoRotate;
    controls.autoRotateSpeed = 0.6;
    controls.target.set(0, 2, 0);
    controlsRef.current = controls;

    // 4. Ground Grid
    const grid = new THREE.GridHelper(80, 50, 0x38bdf8, 0x1e293b);
    grid.position.y = -0.05;
    scene.add(grid);

    // 5. Ambient Dust / Starfield
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

    // 6. Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.85));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(20, 35, 20);
    scene.add(dirLight);

    const cyanLight = new THREE.PointLight(0x38bdf8, 1.0, 60);
    cyanLight.position.set(0, 15, 0);
    scene.add(cyanLight);

    // 7. Selection Ring
    const selRingGeo = new THREE.TorusGeometry(2.1, 0.08, 16, 40);
    const selRingMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const selRing = new THREE.Mesh(selRingGeo, selRingMat);
    selRing.rotation.x = Math.PI / 2;
    selRing.visible = false;
    scene.add(selRing);

    const interactiveMeshes: any[] = [];
    const rotatingMeshes: { mesh: any; rotX?: number; rotY?: number; rotZ?: number }[] = [];
    const flowTracks: { curve: any; particles: any[]; speed: number; reverse?: boolean }[] = [];

    // Helper: Billboard Sprite
    function makeBillboardSprite(title: string, sub: string, colorHex: string, wScale = 6.5, hScale = 3.25) {
      const cvs = document.createElement('canvas');
      cvs.width = 256;
      cvs.height = 128;
      const ctx = cvs.getContext('2d');
      if (!ctx) return new THREE.Sprite();

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

    // Helper: Add Flow Curve with Moving Photons
    function addFlowLine(p1: any, p2: any, colorHex: number, particleCount = 6, speed = 0.007, isCurved = true) {
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

      const pGroup: any[] = [];
      for (let i = 0; i < particleCount; i++) {
        const pGeo = new THREE.SphereGeometry(0.24, 8, 8);
        const pMat = new THREE.MeshBasicMaterial({ color: colorHex });
        const pMesh = new THREE.Mesh(pGeo, pMat);
        pMesh.userData = { t: i / particleCount };
        scene.add(pMesh);
        pGroup.push(pMesh);
      }
      flowTracks.push({ curve, particles: pGroup, speed });
    }

    // 8. Client Node (Octahedron)
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
    rotatingMeshes.push({ mesh: clientMesh, rotY: 0.02, rotX: 0.01 });

    const clientSprite = makeBillboardSprite('客户端集群', 'App Clients', '#38bdf8', 5.5, 2.75);
    clientSprite.position.set(clientPos.x, clientPos.y + 3.0, clientPos.z);
    scene.add(clientSprite);

    // 9. Proxy Gateway Node
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

    const proxyRingGeo = new THREE.TorusGeometry(3.0, 0.08, 16, 36);
    const proxyRingMat = new THREE.MeshBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.8 });
    const proxyRing = new THREE.Mesh(proxyRingGeo, proxyRingMat);
    proxyRing.position.copy(proxyPos);
    proxyRing.rotation.x = Math.PI / 2.5;
    scene.add(proxyRing);
    rotatingMeshes.push({ mesh: proxyRing, rotZ: 0.025 });

    const proxySprite = makeBillboardSprite('fbasecman 网关', '高可用代理中心', '#10b981', 6.8, 3.4);
    proxySprite.position.set(proxyPos.x, proxyPos.y + 2.8, proxyPos.z);
    scene.add(proxySprite);

    // Flow from Client to Proxy (Green)
    addFlowLine(clientPos, proxyPos, 0x10b981, 7, 0.007, false);

    // 10. Compute Cluster Platforms & Nodes from topology
    const groups = Array.from(new Set(topology.nodes.map((n) => n.group || 'cluster')));
    const numGroups = groups.length;
    const clusterPositions: any[] = [];

    if (numGroups === 1) {
      clusterPositions.push(new THREE.Vector3(0, 0, -2));
    } else if (numGroups === 2) {
      clusterPositions.push(new THREE.Vector3(-18, 0, -2));
      clusterPositions.push(new THREE.Vector3(18, 0, -2));
    } else {
      const radius = 20;
      for (let i = 0; i < numGroups; i++) {
        const angle = (i / numGroups) * Math.PI * 2 - Math.PI / 2;
        clusterPositions.push(new THREE.Vector3(radius * Math.cos(angle), 0, radius * Math.sin(angle) - 2));
      }
    }

    const primaryNodePosMap: Record<string, any> = {};

    groups.forEach((groupName, gIdx) => {
      const cPos = clusterPositions[gIdx] || new THREE.Vector3(0, 0, 0);
      const groupNodes = topology.nodes.filter((n) => (n.group || 'cluster') === groupName);

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

      // Platform Glow Ring
      const ringGeo = new THREE.TorusGeometry(8.55, 0.08, 16, 64);
      const ringMat = new THREE.MeshBasicMaterial({ color: 0x0284c7 });
      const platRing = new THREE.Mesh(ringGeo, ringMat);
      platRing.position.set(cPos.x, 0.5, cPos.z);
      platRing.rotation.x = Math.PI / 2;
      scene.add(platRing);

      // Cluster Title Billboard
      const clusterSprite = makeBillboardSprite(
        groupName.toUpperCase(),
        `${groupNodes.length} 个节点集群`,
        '#38bdf8',
        7.2,
        3.6
      );
      clusterSprite.position.set(cPos.x, 6.0, cPos.z - 6.5);
      scene.add(clusterSprite);

      // Find Primary
      const primaryNode = groupNodes.find((n) => n.role === 'primary') || groupNodes[0];
      const standbyNodes = groupNodes.filter((n) => n !== primaryNode);

      let primaryPos: any = null;
      if (primaryNode) {
        primaryPos = new THREE.Vector3(cPos.x, 1.5, cPos.z + 2.8);
        primaryNodePosMap[groupName] = primaryPos;

        const isStopped = observed?.[primaryNode.id]?.running === false;
        const nodeGeo = new THREE.CylinderGeometry(1.65, 1.65, 2.5, 24);
        const nodeMat = isStopped
          ? new THREE.MeshStandardMaterial({ color: 0xef4444, wireframe: true, emissive: 0x7f1d1d })
          : new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.88, roughness: 0.22 });
        const nodeMesh = new THREE.Mesh(nodeGeo, nodeMat);
        nodeMesh.position.copy(primaryPos);
        nodeMesh.userData = { node: primaryNode };
        scene.add(nodeMesh);
        interactiveMeshes.push(nodeMesh);

        // Green LED ring for primary
        const ledGeo = new THREE.TorusGeometry(1.68, 0.12, 16, 32);
        const ledMat = new THREE.MeshBasicMaterial({ color: isStopped ? 0xef4444 : 0x10b981 });
        const ledMesh = new THREE.Mesh(ledGeo, ledMat);
        ledMesh.position.copy(primaryPos);
        ledMesh.rotation.x = Math.PI / 2;
        scene.add(ledMesh);

        const nodeSprite = makeBillboardSprite(primaryNode.label, `${primaryNode.host}:${primaryNode.port}`, '#10b981', 5.6, 2.8);
        nodeSprite.position.set(primaryPos.x, primaryPos.y + 2.6, primaryPos.z);
        scene.add(nodeSprite);

        // Proxy to Primary write route
        addFlowLine(proxyPos, primaryPos, 0x10b981, 6, 0.008, true);
      }

      // Standby Nodes
      standbyNodes.forEach((sbNode, sIdx) => {
        const side = sIdx % 2 === 1 ? -1 : 1;
        const step = Math.ceil((sIdx + 1) / 2);
        const nx = cPos.x + side * (2.8 * step);
        const nz = cPos.z - 2.2;
        const sbPos = new THREE.Vector3(nx, 1.4, nz);

        const isStopped = observed?.[sbNode.id]?.running === false;
        const nodeGeo = new THREE.CylinderGeometry(1.35, 1.35, 2.1, 20);
        const nodeMat = isStopped
          ? new THREE.MeshStandardMaterial({ color: 0xef4444, wireframe: true, emissive: 0x7f1d1d })
          : new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.88, roughness: 0.22 });
        const nodeMesh = new THREE.Mesh(nodeGeo, nodeMat);
        nodeMesh.position.copy(sbPos);
        nodeMesh.userData = { node: sbNode };
        scene.add(nodeMesh);
        interactiveMeshes.push(nodeMesh);

        // Cyan LED ring for standby
        const ledGeo = new THREE.TorusGeometry(1.38, 0.1, 16, 32);
        const ledMat = new THREE.MeshBasicMaterial({ color: isStopped ? 0xef4444 : 0x38bdf8 });
        const ledMesh = new THREE.Mesh(ledGeo, ledMat);
        ledMesh.position.copy(sbPos);
        ledMesh.rotation.x = Math.PI / 2;
        scene.add(ledMesh);

        const nodeSprite = makeBillboardSprite(sbNode.label, `:${sbNode.port}`, '#38bdf8', 4.6, 2.3);
        nodeSprite.position.set(sbPos.x, sbPos.y + 2.3, sbPos.z);
        scene.add(nodeSprite);

        // WAL stream pulse from primary to standby
        if (primaryPos) {
          addFlowLine(primaryPos, sbPos, 0x38bdf8, 5, 0.007, false);
        }
      });
    });

    // 11. Dual MMR Bidirectional Plasma Stream between MMR1 Primary and MMR2 Primary
    const groupKeys = Object.keys(primaryNodePosMap);
    if (groupKeys.length >= 2) {
      const p1 = primaryNodePosMap[groupKeys[0]];
      const p2 = primaryNodePosMap[groupKeys[1]];
      if (p1 && p2) {
        // High-energy Purple Plasma Flow for MMR
        addFlowLine(p1, p2, 0xa855f7, 8, 0.009, true);
        addFlowLine(p2, p1, 0xa855f7, 8, 0.009, true);
      }
    }

    // 12. Click Raycasting
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    function onCanvasClick(event: MouseEvent) {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(interactiveMeshes);
      if (intersects.length > 0) {
        const hit = intersects[0].object;
        if (hit.userData && hit.userData.node) {
          selRing.position.copy(hit.position);
          selRing.position.y += 1.4;
          selRing.visible = true;
          onSelectNode(hit.userData.node);
        }
      }
    }
    renderer.domElement.addEventListener('click', onCanvasClick);

    // 13. Animation Loop
    let animId: number;
    function animate() {
      animId = requestAnimationFrame(animate);

      controls.update();

      // Rotating decorative meshes
      rotatingMeshes.forEach((item) => {
        if (item.rotX) item.mesh.rotation.x += item.rotX;
        if (item.rotY) item.mesh.rotation.y += item.rotY;
        if (item.rotZ) item.mesh.rotation.z += item.rotZ;
      });

      // Flow particle animation along curves
      flowTracks.forEach((track) => {
        track.particles.forEach((pMesh: any) => {
          let t = pMesh.userData.t + track.speed;
          if (t > 1) t -= 1;
          pMesh.userData.t = t;
          const pos = track.curve.getPointAt(t);
          pMesh.position.copy(pos);
        });
      });

      renderer.render(scene, camera);
    }
    animate();

    // Resize handler
    function handleResize() {
      if (!containerRef.current) return;
      const newWidth = containerRef.current.clientWidth;
      camera.aspect = newWidth / canvasHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(newWidth, canvasHeight);
    }
    window.addEventListener('resize', handleResize);

    activeContextRef.current = {
      cleanup: () => {
        cancelAnimationFrame(animId);
        window.removeEventListener('resize', handleResize);
        renderer.domElement.removeEventListener('click', onCanvasClick);
        renderer.dispose();
      },
    };

    return () => {
      activeContextRef.current?.cleanup();
    };
  }, [topology, observed, height, onSelectNode, isAutoRotate]);

  function handleResetView() {
    if (controlsRef.current && cameraRef.current) {
      cameraRef.current.position.set(0, 26, 44);
      controlsRef.current.target.set(0, 2, 0);
      controlsRef.current.update();
    }
  }

  function handleTopView() {
    if (controlsRef.current && cameraRef.current) {
      cameraRef.current.position.set(0, 50, 0.1);
      controlsRef.current.target.set(0, 0, 0);
      controlsRef.current.update();
    }
  }

  function toggleAutoRotate() {
    const next = !isAutoRotate;
    setIsAutoRotate(next);
    if (controlsRef.current) {
      controlsRef.current.autoRotate = next;
    }
  }

  return (
    <div style={{ position: 'relative', width: '100%', borderRadius: 8, overflow: 'hidden', background: '#060913', boxShadow: '0 8px 32px rgba(0, 0, 0, 0.45)', border: '1px solid #1e293b' }}>
      {/* 3D Top Floating Action Toolbar */}
      <div style={{ position: 'absolute', top: 12, left: 16, zIndex: 10, display: 'flex', gap: 8 }}>
        <Button size="small" style={{ background: 'rgba(15, 23, 42, 0.85)', color: '#38bdf8', borderColor: '#0284c7' }} icon={<ReloadOutlined />} onClick={handleResetView}>
          重置视角
        </Button>
        <Button size="small" style={{ background: 'rgba(15, 23, 42, 0.85)', color: '#38bdf8', borderColor: '#0284c7' }} icon={<CompassOutlined />} onClick={handleTopView}>
          俯视全景
        </Button>
        <Button
          size="small"
          style={{ background: isAutoRotate ? 'rgba(16, 185, 129, 0.25)' : 'rgba(15, 23, 42, 0.85)', color: isAutoRotate ? '#34d399' : '#94a3b8', borderColor: isAutoRotate ? '#10b981' : '#334155' }}
          icon={isAutoRotate ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
          onClick={toggleAutoRotate}
        >
          {isAutoRotate ? '巡航中' : '自动巡航'}
        </Button>
      </div>

      {/* 3D Canvas */}
      <div ref={containerRef} style={{ width: '100%', height: typeof height === 'number' ? `${height}px` : height, cursor: 'grab' }} />

      {/* Bottom Floating Legend */}
      <div style={{
        position: 'absolute', bottom: 10, left: 16, right: 16, zIndex: 10,
        display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center', justifyContent: 'center',
        padding: '6px 14px', background: 'rgba(15, 23, 42, 0.82)', backdropFilter: 'blur(8px)',
        borderRadius: 6, border: '1px solid rgba(56, 189, 248, 0.25)', fontSize: 12, color: '#e2e8f0',
      }}>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#10b981', marginRight: 6 }}></span>绿光粒子: 客户端写流 (Primary)</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#a855f7', marginRight: 6 }}></span>紫色等离子: 双向 MMR 对等复制</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#38bdf8', marginRight: 6 }}></span>浅蓝脉冲: WAL 流复制从库</span>
        <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#ef4444', marginRight: 6 }}></span>红色警报: 故障离线</span>
        <span style={{ color: '#94a3b8', marginLeft: 'auto' }}>🖱️ 鼠标左键旋转 · 右键平移 · 滚轮缩放 · 点击节点查看详情</span>
      </div>
    </div>
  );
}
