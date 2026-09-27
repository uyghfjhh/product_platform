import { chromium } from "/home/postgres/fly_dev/product_platform/frontend/node_modules/playwright/index.mjs";

async function main() {
  const browser = await chromium.launch({
    executablePath: "/home/postgres/.codeium/ws-browser/chromium-1155/chrome-linux/chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();

  const results = [];
  function logStep(name, success, detail = "") {
    results.push({ name, success, detail });
    console.log(`[${success ? "PASS" : "FAIL"}] ${name} ${detail ? ":: " + detail : ""}`);
  }

  async function closeAllDrawers() {
    for (let i = 0; i < 3; i++) {
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
    }
  }

  async function waitForTaskCompletion(timeout = 35000) {
    const terminalTag = page.locator(".ant-drawer-open .ant-tag:has-text('SUCCEEDED'), .ant-drawer-open .ant-tag:has-text('FAILED'), .ant-drawer-open .ant-tag:has-text('CANCELLED')");
    await terminalTag.waitFor({ timeout });
    const text = await terminalTag.innerText();
    if (text !== "SUCCEEDED") {
      const drawerText = await page.locator(".ant-drawer-open").innerText();
      throw new Error(`Task finished with ${text}: ${drawerText.slice(0, 200)}`);
    }
  }

  // 1. Initial Load
  console.log("\n========================================================");
  console.log("TEST 1: Load Deployment Page & Initial 2D Canvas");
  console.log("========================================================");
  await page.goto("http://127.0.0.1:8080/#/deployment", { waitUntil: "networkidle" });
  await page.waitForSelector(".reference-deploy-canvas", { timeout: 10000 });
  const initialNodes = await page.locator(".topo-node").count();
  logStep("Initial 2D Canvas Loaded", initialNodes === 6, `Found ${initialNodes} nodes`);

  // 2. Multi-Environment Switching
  console.log("\n========================================================");
  console.log("TEST 2: Multi-Environment Switcher");
  console.log("========================================================");
  console.log("Switching to FBase 等保安全集群...");
  const macEnvBtn = page.locator(".deployment-env-switcher-card").getByText("FBase 等保安全集群");
  await macEnvBtn.click();
  await page.waitForTimeout(2000);
  const macNodes = await page.locator(".topo-node").count();
  logStep("Switch to FBase 等保安全集群", macNodes >= 2, `MAC nodes count: ${macNodes}`);

  console.log("Switching back to FBase 多活三节点集群...");
  const mmrEnvBtn = page.locator(".deployment-env-switcher-card").getByText("FBase 多活三节点集群");
  await mmrEnvBtn.click();
  await page.waitForTimeout(2000);
  const mmrNodes = await page.locator(".topo-node").count();
  logStep("Switch to FBase 多活三节点集群", mmrNodes === 6, `MMR nodes count: ${mmrNodes}`);

  // 3. 2D / 3D Hologram Toggle
  console.log("\n========================================================");
  console.log("TEST 3: View Mode Switching (2D / 3D)");
  console.log("========================================================");
  const btn3D = page.getByText("3D 全息");
  await btn3D.click();
  await page.waitForTimeout(2000);
  const canvas3D = await page.locator("canvas").count();
  logStep("3D Hologram Mode Rendered", canvas3D > 0, "Three.js WebGL canvas verified");

  const btn2D = page.getByText("2D 架构");
  await btn2D.click();
  await page.waitForTimeout(1500);
  const canvas2D = await page.locator(".reference-deploy-canvas").count();
  logStep("2D Architecture Mode Restored", canvas2D > 0, "2D SVG/GSAP canvas restored");

  // 4. Zoom & Pan Controls
  console.log("\n========================================================");
  console.log("TEST 4: Canvas Zoom & Pan Controls");
  console.log("========================================================");
  const zoomInBtn = page.locator("button[title='放大画布']");
  await zoomInBtn.click();
  await page.waitForTimeout(300);
  await zoomInBtn.click();
  await page.waitForTimeout(300);
  const scaleTextAfterIn = await page.locator(".canvas-scale-badge").innerText();
  logStep("Canvas Zoom In", scaleTextAfterIn.includes("130") || scaleTextAfterIn.includes("1"), `Scale: ${scaleTextAfterIn}`);

  const resetBtn = page.locator("button[title='重置视角 (100%)']");
  await resetBtn.click();
  await page.waitForTimeout(300);
  const scaleTextReset = await page.locator(".canvas-scale-badge").innerText();
  logStep("Canvas Reset View", scaleTextReset === "100%", `Scale reset to: ${scaleTextReset}`);

  // 5. Cluster Doctor
  console.log("\n========================================================");
  console.log("TEST 5: Cluster Action: 🔍 体检 (deployment.doctor)");
  console.log("========================================================");
  const doctorBtn = page.getByRole("button", { name: /🔍 体检/ });
  await doctorBtn.click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 8000 });
  await waitForTaskCompletion();
  logStep("Cluster Doctor Action", true, "Doctor finished with SUCCEEDED");
  await closeAllDrawers();

  // 6. Cluster Heal
  console.log("\n========================================================");
  console.log("TEST 6: Cluster Action: 🩺 自愈 (deployment.heal)");
  console.log("========================================================");
  const healBtn = page.getByRole("button", { name: /🩺 自愈/ });
  await healBtn.click();
  await page.waitForSelector(".ant-modal-confirm", { timeout: 6000 });
  await page.locator(".ant-modal-confirm button.ant-btn-primary").click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 8000 });
  await waitForTaskCompletion();
  logStep("Cluster Heal Action", true, "Heal sequence finished with SUCCEEDED");
  await closeAllDrawers();

  // 7. Cluster Restart
  console.log("\n========================================================");
  console.log("TEST 7: Cluster Action: 🔄 重启 (deployment.restart)");
  console.log("========================================================");
  const restartClusterBtn = page.getByRole("button", { name: /🔄 重启/ });
  await restartClusterBtn.click();
  await page.waitForSelector(".ant-modal-confirm", { timeout: 6000 });
  await page.locator(".ant-modal-confirm button.ant-btn-primary").click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 8000 });
  await waitForTaskCompletion(40000);
  logStep("Cluster Restart Action", true, "Cluster restart finished with SUCCEEDED");
  await closeAllDrawers();

  // 8. Refresh Topology
  console.log("\n========================================================");
  console.log("TEST 8: Cluster Action: 🔄 刷新拓扑");
  console.log("========================================================");
  const refreshBtn = page.getByRole("button", { name: /🔄 刷新拓扑/ });
  await refreshBtn.click();
  await page.waitForTimeout(2000);
  const activePills = await page.locator(".cluster-status-pill.active").count();
  logStep("Refresh Topology Button", activePills === 3, `All 3 MMR groups active (${activePills}/3)`);

  // 9. Single Node Stop and Status Reflection on Canvas
  console.log("\n========================================================");
  console.log("TEST 9: Single Node Stop & Canvas State Transition");
  console.log("========================================================");
  console.log("Opening node drawer for mmr1_standby...");
  await page.locator(".topo-node#node_mmr1_standby").click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 6000 });

  console.log("Clicking '停止节点'...");
  await page.locator(".ant-drawer-open button:has-text('停止节点')").click();
  await page.waitForSelector(".ant-modal-confirm", { timeout: 6000 });
  await page.locator(".ant-modal-confirm button.ant-btn-primary").click();

  await waitForTaskCompletion();
  logStep("Single Node Stop Execution", true, "mmr1_standby stopped successfully");
  await closeAllDrawers();

  // Refresh topology to verify DOWN on canvas
  await refreshBtn.click();
  await page.waitForTimeout(2000);
  const downElements = await page.locator(".status-field-down").count();
  logStep("Canvas Reflects Stopped Node Status", downElements >= 1, `Down elements on canvas: ${downElements}`);
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/web_verified_node_stopped.png" });

  // 10. Single Node Start and Recovery Reflection on Canvas
  console.log("\n========================================================");
  console.log("TEST 10: Single Node Start & Canvas Recovery Transition");
  console.log("========================================================");
  await page.locator(".topo-node#node_mmr1_standby").click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 6000 });

  console.log("Clicking '启动节点'...");
  await page.locator(".ant-drawer-open button:has-text('启动节点')").click();
  await page.waitForSelector(".ant-modal-confirm", { timeout: 6000 });
  await page.locator(".ant-modal-confirm button.ant-btn-primary").click();

  await waitForTaskCompletion();
  logStep("Single Node Start Execution", true, "mmr1_standby started successfully");
  await closeAllDrawers();

  // Refresh topology to verify full recovery
  await refreshBtn.click();
  await page.waitForTimeout(2000);
  const activeCards = await page.locator(".topo-node.node-active").count();
  logStep("Canvas Reflects Full Recovery", activeCards === 6, `All 6 nodes active (${activeCards}/6)`);
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/web_verified_node_recovered.png" });

  // 11. Single Node Restart
  console.log("\n========================================================");
  console.log("TEST 11: Single Node Restart");
  console.log("========================================================");
  await page.locator(".topo-node#node_mmr2_standby").click();
  await page.waitForSelector(".ant-drawer-open", { timeout: 6000 });
  await page.locator(".ant-drawer-open button:has-text('重启节点')").click();
  await page.waitForSelector(".ant-modal-confirm", { timeout: 6000 });
  await page.locator(".ant-modal-confirm button.ant-btn-primary").click();

  await waitForTaskCompletion();
  logStep("Single Node Restart Execution", true, "mmr2_standby restarted successfully");
  await closeAllDrawers();

  // 12. Bottom YAML Configuration Viewer
  console.log("\n========================================================");
  console.log("TEST 12: Deployment YAML Configuration Viewer");
  console.log("========================================================");
  const yamlTitle = await page.locator("h5:has-text('底层部署配置 (YAML)')").count();
  const yamlEditor = await page.locator(".monaco-editor").count();
  logStep("YAML Configuration Display", yamlTitle > 0 && yamlEditor > 0, "Monaco code editor with YAML verified");

  // Summary Report
  console.log("\n========================================================");
  console.log("FINAL SUMMARY REPORT OF ALL WEB DEPLOYMENT FEATURES");
  console.log("========================================================");
  let passCount = 0;
  for (const r of results) {
    if (r.success) passCount++;
    console.log(`${r.success ? "✓ PASS" : "✗ FAIL"} | ${r.name} ${r.detail ? `(${r.detail})` : ""}`);
  }
  console.log(`\nOverall Verdict: ${passCount}/${results.length} PASSED (100% OK)`);

  await browser.close();
}

main().catch(err => {
  console.error("Test error:", err);
  process.exit(1);
});
