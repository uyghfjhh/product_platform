// Three.js scene ported from fbasecman_regress_v2/tools/web/app.js.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

  export function mountReferenceThreeScene(container, topology, onSelect) {

    // Setup DOM containers
    container.innerHTML = `
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

    const canvasContainer = container.querySelector('#topo3dCanvas');
    if (!canvasContainer) return () => {};

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

    const controls = new OrbitControls(camera, renderer.domElement);
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
      onSelect(firstNodeData.name || firstNodeData.label);
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
          onSelect(nData.name || nData.label);
          controls.target.set(hit.position.x, hit.position.y, hit.position.z);
        }
      }
    }

    renderer.domElement.addEventListener('click', onPointerClick);

    // -----------------------------------------------------------------------
    // Toolbar Controls Handlers
    // -----------------------------------------------------------------------
    const btnReset = container.querySelector('#btn3dReset');
    if (btnReset) {
      btnReset.addEventListener('click', () => {
        camera.position.set(0, 26, 44);
        controls.target.set(0, 2, 0);
        controls.update();
      });
    }

    const btnTop = container.querySelector('#btn3dTop');
    if (btnTop) {
      btnTop.addEventListener('click', () => {
        camera.position.set(0, 58, 2);
        controls.target.set(0, 0, 0);
        controls.update();
      });
    }

    const btnRotate = container.querySelector('#btn3dRotate');
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

    return () => {
      cancelAnimationFrame(animId);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('click', onPointerClick);
      controls.dispose();
      scene.traverse((object) => {
        if (object.geometry) object.geometry.dispose();
        if (object.material) {
          const materials = Array.isArray(object.material) ? object.material : [object.material];
          materials.forEach((material) => {
            if (material.map) material.map.dispose();
            material.dispose();
          });
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }

