import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Run against an isolated platform with seeded results; never execute database tests.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18767';
const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  for (const [suite, target] of [
    ['mac', 'mac.audit.log_access_restrictions'],
    ['mmr', 'mmr.background.maintenance_lifecycle'],
  ]) {
    const page = await browser.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    const bound = await page.request.put(`${base}/api/v1/regression-bindings/fbase-database/${suite}`, {
      data: { environment_id: `report-${suite}` },
    });
    assert.equal(bound.status(), 200, await bound.text());
    await page.addInitScript((mode) => localStorage.setItem('platform-page', `tests:fbase-database:${mode}`), suite);
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder(/搜索用例名称/).fill(target);
    const row = page.locator('.case-row').filter({ has: page.locator(`.case-name[title="${target}"]`) });
    await row.getByRole('button', { name: '📄 查看报告', exact: true }).click();
    const modal = page.getByRole('dialog');
    await modal.getByText(suite==='mmr'?'验证维护进程创建节点后启动、分离后退出且不重启':'验证审计日志访问权限边界',{exact:true}).waitFor();
    assert.equal(await modal.getByText('1 个步骤的断言全部通过',{exact:true}).count(),0);
    await modal.getByText('检查步骤验收', { exact: false }).waitFor();
    await modal.getByText('disabled', { exact: true }).waitFor();
    await modal.getByText('psql -c', { exact: false }).waitFor();
    await modal.getByText('结果分析', { exact: true }).waitFor();
    await modal.getByText('实际结果不满足声明期望', { exact: false }).waitFor();
    assert.equal(await modal.getByText('断言规则', { exact: true }).count(), 0);
    assert.equal(await modal.getByRole('link', { name: /command-1.json/ }).count(), 0);
    await modal.getByText('技术附件', { exact: true }).click();
    const evidence = await modal.getByRole('link', { name: /command-1.json/ }).getAttribute('href');
    const evidenceResponse = await page.request.get(base + evidence);
    assert.equal(evidenceResponse.status(), 200);
    assert.match(await evidenceResponse.text(), /state=ready/);
    await modal.getByRole('tab', { name: '原始报告' }).click();
    await modal.getByText('浏览器原始报告验收', { exact: true }).waitFor();
    for (const theme of ['cman', 'dark', 'soft', 'warm']) {
      const colors = await modal.locator('.raw-report').evaluate((element, theme) => {
        document.documentElement.dataset.theme = theme;
        const style = getComputedStyle(element);
        return { color: style.color, background: style.backgroundColor };
      }, theme);
      const luminance = (color) => {
        const channels = color.match(/[\d.]+/g).slice(0, 3).map((value) => {
          const channel = Number(value) / 255;
          return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
        });
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      };
      const foreground = luminance(colors.color), background = luminance(colors.background);
      const contrast = (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
      assert.ok(contrast >= 4.5, `Raw report is readable in ${theme}: contrast ${contrast.toFixed(2)}`);
      assert.equal(await modal.locator('.raw-report').textContent(), '浏览器原始报告验收');
    }
    const download = await modal.getByRole('link', { name: '下载原始报告' }).getAttribute('href');
    const response = await page.request.get(base + download);
    assert.equal(response.status(), 200);
    assert.equal(await response.text(), '浏览器原始报告验收');
    const html = await page.getByRole('link', { name: '📄 导出 HTML' }).getAttribute('href');
    assert.equal((await page.request.get(base + html)).status(), 200);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log('Report browser checks passed: MAC/MMR steps, raw report, downloads and HTML export');
} finally {
  await browser.close();
}
