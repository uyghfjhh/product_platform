import { chromium } from "/home/postgres/fly_dev/product_platform/frontend/node_modules/playwright/index.mjs";

async function main() {
  const browser = await chromium.launch({
    executablePath: "/home/postgres/.codeium/ws-browser/chromium-1155/chrome-linux/chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  await page.addInitScript(() => {
    localStorage.setItem("platform-page", "deployment");
    localStorage.setItem("platform-environment", "cman-lab");
    localStorage.setItem("platform-theme", "cman");
  });

  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  const wizardBtn = page.locator("button:has-text('部署向导')");
  console.log("Wizard button exists:", await wizardBtn.count());
  await wizardBtn.click();
  await page.waitForTimeout(1000);

  const modals = await page.evaluate(() => {
    return Array.from(document.querySelectorAll(".ant-modal, .ant-modal-content, [role='dialog']")).map(el => ({
      tagName: el.tagName,
      className: el.className,
      text: el.innerText.slice(0, 100),
      display: window.getComputedStyle(el).display,
      visibility: window.getComputedStyle(el).visibility,
    }));
  });
  console.log("Found modal elements in DOM:", JSON.stringify(modals, null, 2));

  await browser.close();
}

main().catch(console.error);
