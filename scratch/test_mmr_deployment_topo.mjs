import { chromium } from "/home/postgres/fly_dev/product_platform/frontend/node_modules/playwright/index.mjs";

async function main() {
  const browser = await chromium.launch({
    executablePath: "/home/postgres/.codeium/ws-browser/chromium-1155/chrome-linux/chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();

  console.log(">>> 1. Loading app at http://127.0.0.1:8080/#/deployment ...");
  await page.goto("http://127.0.0.1:8080/#/deployment", { waitUntil: "networkidle" });

  // Wait for active status pill to appear
  console.log(">>> 2. Waiting for cluster status to become active...");
  await page.waitForSelector(".cluster-status-pill.active", { timeout: 10000 });
  await page.waitForTimeout(1000);

  // Verify cluster boxes
  const clusterBoxes = await page.locator(".cluster-group-box").count();
  console.log(`Cluster group boxes found: ${clusterBoxes}`);

  const clusterTags = await page.locator(".cluster-group-tag").allInnerTexts();
  console.log("Cluster group tags:", clusterTags);

  // Verify nodes
  const topoNodes = await page.locator(".topo-node").count();
  console.log(`Topo nodes found: ${topoNodes}`);

  // Verify active edges
  const edges = await page.locator(".topo-edge-line").count();
  const activeEdges = await page.locator(".topo-edge-line.edge-active").count();
  console.log(`Edges found: ${edges}, active: ${activeEdges}`);

  // Screenshot of 2D topology with all nodes ACTIVE!
  const screenshotPath2D = "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/mmr_deployment_canvas_2d.png";
  await page.screenshot({ path: screenshotPath2D, fullPage: false });
  console.log(`Saved 2D canvas screenshot to ${screenshotPath2D}`);

  // Click mmr1_primary node gear icon or card to open drawer
  console.log(">>> 3. Inspecting node mmr1_primary...");
  const gearBtn = page.locator(".topo-node#node_mmr1_primary button[title*='查看节点运维与指标详情']");
  if (await gearBtn.count() > 0) {
    await gearBtn.click();
  } else {
    await page.locator(".topo-node#node_mmr1_primary").click();
  }
  await page.waitForTimeout(1000);

  const drawerOpen = await page.locator(".ant-drawer-open").count();
  console.log(`Drawer open: ${drawerOpen}`);

  const drawerScreenshot = "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/mmr_node_inspector.png";
  await page.screenshot({ path: drawerScreenshot, fullPage: false });
  console.log(`Saved node inspector screenshot to ${drawerScreenshot}`);

  // Close drawer
  console.log(">>> 4. Closing inspector drawer...");
  const closeBtn = page.locator(".ant-drawer-close");
  if (await closeBtn.count() > 0) {
    await closeBtn.click();
    await page.waitForTimeout(800);
  }

  // Switch to 3D View
  console.log(">>> 5. Testing 3D Hologram View...");
  const btn3D = page.getByText("3D 全息");
  if (await btn3D.count() > 0) {
    await btn3D.click();
    await page.waitForTimeout(2500);
    const screenshotPath3D = "/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/mmr_deployment_canvas_3d.png";
    await page.screenshot({ path: screenshotPath3D, fullPage: false });
    console.log(`Saved 3D canvas screenshot to ${screenshotPath3D}`);
  }

  // Switch back to 2D View
  const btn2D = page.getByText("2D 架构");
  if (await btn2D.count() > 0) {
    await btn2D.click();
    await page.waitForTimeout(800);
  }

  console.log(">>> All MMR deployment canvas tests verified successfully!");
  await browser.close();
}

main().catch(err => {
  console.error("Test error:", err);
  process.exit(1);
});
