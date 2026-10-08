import { chromium } from 'playwright';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto('http://127.0.0.1:8080', { waitUntil: 'domcontentloaded' });
await page.locator('.header-title', { hasText: '数据库部署管理' }).waitFor();
await page.waitForTimeout(1500);
const offenders = await page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const bad = [];
    document.querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > limit + 1 || r.right > limit + 1 && r.left >= 0) {
            bad.push(`${el.tagName}.${String(el.className).slice(0,60)} w=${Math.round(r.width)} right=${Math.round(r.right)}`);
        }
    });
    return bad.slice(0, 15);
});
console.log(offenders.join('\n'));
await page.screenshot({ path: '/tmp/mobile_overflow.png', fullPage: true });
await browser.close();
