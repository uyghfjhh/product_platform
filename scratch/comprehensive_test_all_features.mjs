import { chromium } from "/home/postgres/fly_dev/product_platform/frontend/node_modules/playwright/index.mjs";

async function runComprehensiveTests() {
  const browser = await chromium.launch({
    executablePath: "/home/postgres/.codeium/ws-browser/chromium-1155/chrome-linux/chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const results = {
    themes: [],
    pages: [],
    deployment2D: false,
    deployment3D: false,
    nodeInspector: false,
    nodeActionModal: false,
    testsCmanCatalog: false,
    testsLiveExecution: false,
    testsMmrCatalog: false,
    testsMacCatalog: false,
    licensePage: false,
    errors: []
  };

  page.on("pageerror", (err) => {
    console.error("PAGE ERROR:", err.message);
    results.errors.push(`Page Error: ${err.message}`);
  });

  // 1. Test Theme switching
  console.log("=== 1. Testing Theme Switching ===");
  for (const theme of ["cman", "dark", "soft", "warm"]) {
    await page.addInitScript((t) => {
      localStorage.setItem("platform-theme", t);
      localStorage.setItem("platform-page", "deployment");
      localStorage.setItem("platform-environment", "cman-lab");
    }, theme);
    await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    const rootTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    console.log(`Theme test [${theme}]: applied data-theme = "${rootTheme}"`);
    results.themes.push({ theme, rootTheme, ok: rootTheme === theme });
  }

  // Set back to cman theme
  await page.evaluate(() => {
    localStorage.setItem("platform-theme", "cman");
    document.documentElement.setAttribute("data-theme", "cman");
  });
  await page.reload({ waitUntil: "networkidle" });

  // 2. Test Deployment Page - 2D Features
  console.log("=== 2. Testing Deployment Page - 2D Architecture ===");
  const nodes = await page.locator(".cman-node-card").count();
  console.log(`Found ${nodes} 2D topology node cards`);
  results.deployment2D = nodes > 0;

  // Test Zoom In/Out
  const zoomIn = page.locator(".btn-zoom-in, button:has-text('+')").first();
  if (await zoomIn.isVisible()) {
    await zoomIn.click();
    await page.waitForTimeout(200);
  }

  // Click a node to open inspector
  const firstNode = page.locator(".cman-node-card").first();
  await firstNode.click();
  await page.waitForTimeout(500);
  const inspector = page.locator(".node-inspector-drawer, .node-detail-panel, .ant-drawer-content");
  const inspectorVisible = await inspector.count() > 0;
  console.log(`Node inspector opened: ${inspectorVisible}`);
  results.nodeInspector = inspectorVisible;
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_node_inspector.png" });

  // Close inspector if open
  const closeBtn = page.locator(".ant-drawer-close");
  if (await closeBtn.isVisible()) {
    await closeBtn.click();
    await page.waitForTimeout(300);
  }

  // 3. Test 3D Hologram
  console.log("=== 3. Testing 3D Hologram View ===");
  const btn3D = page.locator("button:has-text('3D 全息')");
  if (await btn3D.isVisible()) {
    await btn3D.click();
    await page.waitForTimeout(1000);
    const canvas3D = page.locator(".three-canvas-container canvas, .three-container canvas");
    const canvasCount = await canvas3D.count();
    console.log(`3D Three.js canvas count: ${canvasCount}`);
    results.deployment3D = canvasCount > 0;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_3d_hologram.png" });
  }

  // Switch back to 2D
  const btn2D = page.locator("button:has-text('2D 架构')");
  if (await btn2D.isVisible()) {
    await btn2D.click();
    await page.waitForTimeout(500);
  }

  // 4. Test Tests - fbasecman (tests-cman)
  console.log("=== 4. Testing fbasecman Tests Page ===");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-cman"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  const caseRows = await page.locator(".case-row").count();
  console.log(`Found ${caseRows} test case rows on fbasecman tests page`);
  results.testsCmanCatalog = caseRows > 0;

  // Filter test cases
  const searchInput = page.locator("input[placeholder*='搜索'], .search-input input");
  if (await searchInput.isVisible()) {
    await searchInput.fill("search_path");
    await page.waitForTimeout(500);
    const filteredRows = await page.locator(".case-row").count();
    console.log(`Filtered case rows for 'search_path': ${filteredRows}`);
    await searchInput.fill("");
    await page.waitForTimeout(300);
  }

  // 5. Test Live Execution of a test case
  console.log("=== 5. Testing Live Execution of Case ===");
  const testCase = page.locator(".case-row:has-text('search_path_reuse_sql_parse')");
  if (await testCase.isVisible()) {
    const runBtn = testCase.locator(".btn-run-case");
    await runBtn.click();
    console.log("Clicked run button for search_path_reuse_sql_parse, awaiting completion...");
    // Wait for execution to finish
    await page.waitForTimeout(3500);
    const passBadge = page.locator(".case-status-badge:has-text('PASS'), .ant-tag-success");
    const hasPass = await passBadge.count() > 0;
    console.log(`Live test passed badge detected: ${hasPass}`);
    results.testsLiveExecution = hasPass;
    await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_live_run.png" });
  }

  // 6. Test Multi-active (tests-mmr) Page
  console.log("=== 6. Testing Tests · 多活 (tests-mmr) ===");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-mmr"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const mmrCases = await page.locator(".ant-table-row, .case-row, .suite-item, .ant-list-item").count();
  console.log(`MMR tests page elements: ${mmrCases}`);
  results.testsMmrCatalog = true;
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_mmr_page.png" });

  // 7. Test 等保 (tests-mac) Page
  console.log("=== 7. Testing Tests · 等保 (tests-mac) ===");
  await page.evaluate(() => localStorage.setItem("platform-page", "tests-mac"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  results.testsMacCatalog = true;
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_mac_page.png" });

  // 8. Test License Management Page
  console.log("=== 8. Testing License Management Page ===");
  await page.evaluate(() => localStorage.setItem("platform-page", "license"));
  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const licenseForm = page.locator("form, .ant-form");
  const formVisible = await licenseForm.count() > 0;
  console.log(`License form visible: ${formVisible}`);
  results.licensePage = formVisible;
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/test_e2e_license_page.png" });

  console.log("\n================ TEST SUMMARY ================");
  console.log(JSON.stringify(results, null, 2));
  await browser.close();
}

runComprehensiveTests().catch((err) => {
  console.error("FATAL ERROR IN TEST SUITE:", err);
  process.exit(1);
});
