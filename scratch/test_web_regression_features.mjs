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

  async function waitForTaskCompletion(timeout = 40000) {
    const terminalTag = page.locator(".ant-drawer-open .ant-tag:has-text('SUCCEEDED'), .ant-drawer-open .ant-tag:has-text('FAILED'), .ant-drawer-open .ant-tag:has-text('CANCELLED')");
    await terminalTag.waitFor({ timeout });
    const text = await terminalTag.innerText();
    if (text !== "SUCCEEDED") {
      const drawerText = await page.locator(".ant-drawer-open").innerText();
      throw new Error(`Task finished with ${text}: ${drawerText.slice(0, 300)}`);
    }
  }

  console.log("\n========================================================");
  console.log("TEST 1: MMR (多活) Web Regression Testing");
  console.log("========================================================");
  await page.goto("http://127.0.0.1:8080/", { waitUntil: "networkidle" });
  await page.waitForSelector(".ant-menu", { timeout: 10000 });

  // Open "测试" submenu if not open
  const testSubmenu = page.locator(".ant-menu-submenu-title:has-text('测试')");
  if (await testSubmenu.isVisible()) {
    const isExpanded = await page.locator(".ant-menu-submenu-open:has-text('测试')").count();
    if (!isExpanded) {
      await testSubmenu.click();
      await page.waitForTimeout(500);
    }
  }

  // Click "多活" menu item
  console.log("Clicking menu item: 测试 -> 多活...");
  const mmrMenu = page.getByRole("menuitem", { name: "多活", exact: true });
  await mmrMenu.click();
  await page.waitForSelector(".case-row", { timeout: 15000 });
  logStep("MMR Tests Page Loaded", true, "Found .case-row elements");

  // Verify total count
  const totalCountEl = page.locator(".metric-card.card-total .metric-value");
  const totalCount = await totalCountEl.innerText();
  logStep("MMR Total Cases Discovered", parseInt(totalCount) >= 150, `Found ${totalCount} cases`);

  // Search for subscription_control
  const searchInput = page.locator(".search-box input");
  await searchInput.fill("subscription_control");
  await page.waitForTimeout(1000);

  // Find the case row for mmr.subscription_control.enable_disable
  const mmrCaseRow = page.locator(".case-row:has-text('mmr.subscription_control.enable_disable')");
  await mmrCaseRow.waitFor({ timeout: 5000 });
  logStep("Filter MMR Case Row", await mmrCaseRow.isVisible(), "Found mmr.subscription_control.enable_disable");

  // Click Run on MMR case
  console.log("Executing MMR case: mmr.subscription_control.enable_disable from Web UI...");
  const runBtn = mmrCaseRow.locator(".btn-run-case");
  await runBtn.click();

  // Wait for task to open and complete
  await waitForTaskCompletion(25000);
  logStep("MMR Case Task Executed", true, "Task completed with SUCCEEDED");

  // Close drawer and verify PASS status badge in case row
  await closeAllDrawers();
  const statusBadge = mmrCaseRow.locator(".result-badge:has-text('PASS')");
  await statusBadge.waitFor({ timeout: 15000 });
  const badgeText = await statusBadge.innerText();
  logStep("MMR Case Status Updated in Web UI", badgeText.includes("PASS"), `Status: ${badgeText}`);

  // Take screenshot of MMR Tests Page highlighting passed case
  await page.waitForTimeout(500);
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/web_verified_mmr_tests.png" });
  logStep("Captured MMR Web Tests Screenshot", true);


  console.log("\n========================================================");
  console.log("TEST 2: MAC (等保) Web Regression Testing");
  console.log("========================================================");
  // Click "等保" menu item
  console.log("Clicking menu item: 测试 -> 等保...");
  const macMenu = page.getByRole("menuitem", { name: "等保", exact: true });
  await macMenu.click();
  await page.waitForSelector(".case-row", { timeout: 15000 });
  logStep("MAC Tests Page Loaded", true, "Found .case-row elements");

  const macTotalCountEl = page.locator(".metric-card.card-total .metric-value");
  const macTotalCount = await macTotalCountEl.innerText();
  logStep("MAC Total Cases Discovered", parseInt(macTotalCount) >= 50, `Found ${macTotalCount} cases`);

  // Search for enable_audit
  const macSearchInput = page.locator(".search-box input");
  await macSearchInput.fill("enable_audit");
  await page.waitForTimeout(1000);

  const macCaseRow = page.locator(".case-row:has-text('mac.audit.enable_audit')");
  await macCaseRow.waitFor({ timeout: 5000 });
  logStep("Filter MAC Case Row", await macCaseRow.isVisible(), "Found mac.audit.enable_audit");

  // Click Run on MAC case
  console.log("Executing MAC case: mac.audit.enable_audit from Web UI...");
  const macRunBtn = macCaseRow.locator(".btn-run-case");
  await macRunBtn.click();

  // Wait for task completion
  await waitForTaskCompletion(25000);
  logStep("MAC Case Task Executed", true, "Task completed with SUCCEEDED");

  await closeAllDrawers();
  const macStatusBadge = macCaseRow.locator(".result-badge:has-text('PASS')");
  await macStatusBadge.waitFor({ timeout: 15000 });
  const macBadgeText = await macStatusBadge.innerText();
  logStep("MAC Case Status Updated in Web UI", macBadgeText.includes("PASS"), `Status: ${macBadgeText}`);

  // Take screenshot of MAC Tests Page
  await macSearchInput.fill("");
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/web_verified_mac_tests.png" });
  logStep("Captured MAC Web Tests Screenshot", true);

  await browser.close();

  console.log("\n========================================================");
  console.log("REGRESSION WEB TEST SUMMARY");
  console.log("========================================================");
  const passed = results.filter(r => r.success).length;
  console.log(`Passed: ${passed}/${results.length}`);
  if (passed !== results.length) {
    process.exit(1);
  }
}

main().catch(err => {
  console.error("Test execution failed:", err);
  process.exit(1);
});
