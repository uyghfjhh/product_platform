import { chromium } from "/home/postgres/fly_dev/product_platform/frontend/node_modules/playwright/index.mjs";

async function runComprehensiveTests() {
  const browser = await chromium.launch({
    executablePath: "/home/postgres/.codeium/ws-browser/chromium-1155/chrome-linux/chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const report = {
    themes: [],
    deployment2D: { nodeCount: 0, inspectorOpened: false, zoomWorked: false },
    deployment3D: { canvasRendered: false },
    testsCman: { caseCount: 0, searchFilterPassed: false, liveExecutionPassed: false, failModalOpened: false },
    testsMmr: { loaded: false, title: "" },
    testsMac: { loaded: false, title: "" },
    license: { formRendered: false, versionOptions: 0 },
    errors: []
  };

  page.on("pageerror", (err) => {
    report.errors.push(`Page Error: ${err.message}`);
  });

  console.log("==================================================");
  console.log("🚀 STARTING COMPREHENSIVE END-TO-END WEB APP AUDIT");
  console.log("==================================================");

  // 1. THEME SWITCHING TEST (All 4 themes)
  console.log("\n[TEST 1] Testing all 4 Themes...");
  const themes = [
    { key: "cman", name: "极客夜蓝" },
    { key: "dark", name: "深色石墨" },
    { key: "soft", name: "柔和灰绿" },
    { key: "warm", name: "暖灰护眼" }
  ];

  for (const t of themes) {
    await page.addInitScript((val) => {
      localStorage.setItem("platform-theme", val);
      localStorage.setItem("platform-page", "deployment");
      localStorage.setItem("platform-environment", "cman-lab");
    }, t.key);
    await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
    await page.waitForTimeout(400);

    const activeTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    const matches = activeTheme === t.key;
    console.log(`  ✓ Theme ${t.name} (${t.key}): DOM data-theme="${activeTheme}" [${matches ? "OK" : "FAIL"}]`);
    report.themes.push({ ...t, activeTheme, ok: matches });
  }

  // Set to cman theme for subsequent tests
  await page.evaluate(() => {
    localStorage.setItem("platform-theme", "cman");
    document.documentElement.setAttribute("data-theme", "cman");
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  // 2. DEPLOYMENT PAGE - 2D GSAP / SVG TOPOLOGY
  console.log("\n[TEST 2] Testing Deployment Page - 2D GSAP/SVG Topology...");
  const topoNodes = await page.locator(".topo-node, [data-node-id]").count();
  console.log(`  ✓ Detected ${topoNodes} active topology node cards`);
  report.deployment2D.nodeCount = topoNodes;

  // Zoom controls
  const zoomIn = page.locator(".canvas-btn:has-text('+'), button:has-text('+')").first();
  if (await zoomIn.isVisible()) {
    await zoomIn.click();
    await page.waitForTimeout(300);
    const scaleBadge = page.locator(".canvas-scale-badge");
    const badgeText = (await scaleBadge.count()) > 0 ? await scaleBadge.innerText() : "";
    console.log(`  ✓ Zoom button clicked, current scale badge: "${badgeText}"`);
    report.deployment2D.zoomWorked = true;
  }

  // Node Click -> Inspector Drawer
  const targetNode = page.locator(".topo-node, [data-node-id]").first();
  if (await targetNode.isVisible()) {
    console.log("  ✓ Clicking node to open inspector drawer...");
    await targetNode.click();
    await page.waitForTimeout(600);
    const drawer = page.locator(".ant-drawer-content");
    const isOpen = (await drawer.count()) > 0;
    console.log(`  ✓ Node inspector drawer visible: ${isOpen}`);
    report.deployment2D.inspectorOpened = isOpen;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_2d_inspector.png" });

    // Close drawer
    const closeBtn = page.locator(".ant-drawer-close");
    if (await closeBtn.isVisible()) {
      await closeBtn.click();
      await page.waitForTimeout(300);
    }
  }

  // 3. DEPLOYMENT PAGE - 3D THREE.JS HOLOGRAM
  console.log("\n[TEST 3] Testing 3D Three.js Hologram View...");
  const btn3D = page.locator("button:has-text('3D 全息')");
  if (await btn3D.isVisible()) {
    await btn3D.click();
    await page.waitForTimeout(1500);
    const canvas3D = page.locator("canvas");
    const hasCanvas = (await canvas3D.count()) > 0;
    console.log(`  ✓ Three.js WebGL Canvas mounted: ${hasCanvas}`);
    report.deployment3D.canvasRendered = hasCanvas;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_3d_hologram.png" });

    // Switch back to 2D
    const btn2D = page.locator("button:has-text('2D 架构')");
    if (await btn2D.isVisible()) {
      await btn2D.click();
      await page.waitForTimeout(400);
    }
  }

  // 4. TESTS PAGE - fbasecman (tests-cman)
  console.log("\n[TEST 4] Testing fbasecman Regression Tests Page...");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-cman"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  const caseRows = await page.locator(".case-row").count();
  console.log(`  ✓ Detected ${caseRows} test cases in catalog`);
  report.testsCman.caseCount = caseRows;

  // Filter test cases
  const searchInput = page.locator("input[placeholder*='搜索']");
  if (await searchInput.isVisible()) {
    await searchInput.fill("search_path");
    await page.waitForTimeout(400);
    const filtered = await page.locator(".case-row").count();
    console.log(`  ✓ Filter search 'search_path': ${filtered} matching cases found`);
    report.testsCman.searchFilterPassed = filtered > 0;
    await searchInput.fill("");
    await page.waitForTimeout(300);
  }

  // Live Run Case
  console.log("  ✓ Triggering live execution of test case 'guc.search_path_reuse_sql_parse'...");
  const caseToRun = page.locator(".case-row:has-text('search_path_reuse_sql_parse')");
  if (await caseToRun.isVisible()) {
    const runBtn = caseToRun.locator(".btn-run-case");
    await runBtn.click();
    // Wait for live run to complete
    await page.waitForTimeout(4000);
    const passTag = page.locator(".case-status-badge:has-text('PASS'), .ant-tag:has-text('PASS')");
    const passed = (await passTag.count()) > 0;
    console.log(`  ✓ Live execution completed, status PASS detected: ${passed}`);
    report.testsCman.liveExecutionPassed = passed;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_live_run_passed.png" });
  }

  // Failure modal check
  const failCard = page.locator(".card-fail");
  if (await failCard.count() > 0) {
    console.log("  ✓ Clicking failed test card to verify failure modal details...");
    await failCard.first().click();
    await page.waitForTimeout(600);
    const modal = page.locator(".ant-modal-content, .failed-modal");
    const modalOpen = (await modal.count()) > 0;
    console.log(`  ✓ Failure modal visible: ${modalOpen}`);
    report.testsCman.failModalOpened = modalOpen;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_fail_modal.png" });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  }

  // 5. TESTS PAGE - 多活 (tests-mmr)
  console.log("\n[TEST 5] Testing Tests · 多活 (tests-mmr)...");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-mmr"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const mmrHeader = await page.locator(".header-title, .page-heading").innerText();
  console.log(`  ✓ MMR Page loaded: header text = "${mmrHeader.trim()}"`);
  report.testsMmr = { loaded: true, title: mmrHeader.trim() };
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_mmr_page.png" });

  // 6. TESTS PAGE - 等保 (tests-mac)
  console.log("\n[TEST 6] Testing Tests · 等保 (tests-mac)...");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-mac"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const macHeader = await page.locator(".header-title, .page-heading").innerText();
  console.log(`  ✓ MAC Page loaded: header text = "${macHeader.trim()}"`);
  report.testsMac = { loaded: true, title: macHeader.trim() };
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_mac_page.png" });

  // 7. LICENSE MANAGEMENT PAGE
  console.log("\n[TEST 7] Testing License Management Page...");
  await page.evaluate(() => localStorage.setItem("platform-page", "license"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const form = page.locator("form.ant-form");
  const formRendered = (await form.count()) > 0;
  console.log(`  ✓ License form mounted: ${formRendered}`);
  report.license.formRendered = formRendered;
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/audit_license_page.png" });

  console.log("\n==================================================");
  console.log("🏁 ALL TESTS FINISHED. FULL REPORT:");
  console.log("==================================================");
  console.log(JSON.stringify(report, null, 2));

  await browser.close();
}

runComprehensiveTests().catch((err) => {
  console.error("TEST EXECUTION ERROR:", err);
  process.exit(1);
});
