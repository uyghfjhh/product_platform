import { chromium } from 'playwright';
import path from 'path';

const base = 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  console.log('Navigating to', base);
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  // Take screenshot of initial deployment page (fbasecman)
  await page.screenshot({ path: '/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/deploy_cman.png' });
  console.log('Saved deploy_cman.png');

  // Find the environment switcher Segmented buttons
  console.log('Checking environment options...');
  const envButtons = page.locator('.deployment-env-switcher-card .ant-segmented-item');
  const count = await envButtons.count();
  console.log(`Found ${count} environment buttons`);

  for (let i = 0; i < count; i++) {
    const text = await envButtons.nth(i).innerText();
    console.log(`Env button ${i}: ${text.replace(/\n/g, ' ')}`);
  }

  // Click on 等保安全集群
  console.log('Switching to 等保安全集群...');
  await page.locator('.deployment-env-switcher-card .ant-segmented-item:has-text("等保")').click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: '/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/deploy_mac.png' });
  console.log('Saved deploy_mac.png');

  // Click on 多活三节点集群
  console.log('Switching to 多活三节点集群...');
  await page.locator('.deployment-env-switcher-card .ant-segmented-item:has-text("多活")').click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: '/home/postgres/.gemini/antigravity-ide/brain/68f53022-4011-4b80-ab70-7698f8638705/deploy_mmr.png' });
  console.log('Saved deploy_mmr.png');

  // Check left menu items
  const menuItems = await page.locator('.ant-menu-item').allInnerTexts();
  console.log('Left menu items:', menuItems.map(s => s.replace(/\n/g, ' ')));

} catch (err) {
  console.error('Error during test:', err);
} finally {
  await browser.close();
}
