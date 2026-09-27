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
    localStorage.setItem("platform-page", "tests-cman");
    localStorage.setItem("platform-environment", "cman-lab");
    localStorage.setItem("platform-theme", "cman");
  });

  await page.goto("http://127.0.0.1:8080", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  const failCard = page.locator(".card-fail");
  console.log("Fail card exists:", await failCard.count());
  await failCard.click();
  await page.waitForTimeout(1000);

  const modals = await page.evaluate(() => {
    return Array.from(document.querySelectorAll(".ant-modal")).map(el => ({
      className: el.className,
      text: el.innerText.slice(0, 100),
      display: window.getComputedStyle(el).display,
    }));
  });
  console.log("Found modal elements for failCard:", JSON.stringify(modals, null, 2));

  await browser.close();
}

main().catch(console.error);
