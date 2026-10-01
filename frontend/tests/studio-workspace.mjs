import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { mockTablesQuery, mockTimezoneQuery } from '@prisma/studio-core/data/postgres-core';

// A fixture handshake and mocked Studio executor keep this independent of real databases.
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:18769';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const handshake = await page.request.get(`${base}/api/v1/deployment-browser-fixture`);
  assert.equal(handshake.status(), 200, 'Use only the isolated fixture service');
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  const environmentId = `studio-browser-${Date.now()}`;
  const created = await page.request.post(`${base}/api/v1/environments`, { data: {
    id: environmentId, product_id: 'demo', title: 'Studio 浏览器验收',
    host: '127.0.0.1', port: 7000, database_name: 'postgres', database_user: 'postgres',
  } });
  assert.equal(created.status(), 201);
  await page.route(`**/api/v1/environments/${environmentId}/topology`, route => route.fulfill({ json: { nodes: [
    { id: 'primary', label: '主库', host: '127.0.0.1', port: 7000, role: 'primary', data_dir: '/tmp/p' },
    { id: 'standby', label: '远端备库', host: '10.0.0.2', port: 7000, role: 'standby', data_dir: '/tmp/s' },
  ] } }));
  await page.route(`**/api/v1/environments/${environmentId}/topology/status`, route => route.fulfill({ json: {
    primary: { running: true }, standby: { running: true },
  } }));
  await page.route(new RegExp(`/api/v1/environments/${environmentId}/studio(?:/nodes/[^/?]+)?$`), route => {
    const body = route.request().postDataJSON();
    requests.push(route.request().url());
    const result = body.procedure === 'sequence'
      ? [[null, mockTablesQuery()], [null, mockTimezoneQuery()]]
      : body.procedure === 'sql-lint' ? [null, { diagnostics: [] }]
        : /pg_class|pg_attribute|pg_namespace/.test(body.query?.sql || '') ? [null, mockTablesQuery()]
          : /timezone|time zone/i.test(body.query?.sql || '') ? [null, mockTimezoneQuery()]
            : [null, [{ id: 1, email: 'fixture@example.test', __ps_count__: '1' }]];
    return route.fulfill({ json: result });
  });
  await page.addInitScript(({ environmentId }) => {
    localStorage.setItem('platform-page', 'database');
    localStorage.setItem('platform-environment', environmentId);
    localStorage.setItem('platform-theme', 'cman');
  }, { environmentId });
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('studio-shell').waitFor();
  await page.locator('.database-studio').getByText('users', { exact: true }).first().waitFor();
  assert.equal(await page.locator('.database-studio').count(), 1);
  for (const text of ['快捷模板:', 'SQL 交互编辑器', '实例运行状态', '数据管理器']) {
    assert.equal(await page.getByText(text, { exact: true }).count(), 0, `Removed duplicate UI: ${text}`);
  }
  for (const theme of ['cman', 'dark', 'soft', 'warm']) {
    const colors = await page.evaluate(theme => {
      document.documentElement.dataset.theme = theme;
      const probe = document.createElement('span');
      probe.style.backgroundColor = 'var(--bg-primary)';
      document.body.append(probe);
      const expected = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return { expected, actual: getComputedStyle(document.querySelector('[data-testid="studio-shell"]')).backgroundColor };
    }, theme);
    assert.equal(colors.actual, colors.expected, `Studio follows ${theme} palette`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'cman'; });
  await page.getByRole('combobox', { name: '选择目标实例' }).click();
  await page.locator('.ant-select-item-option-content').filter({ hasText: '远端备库' }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="studio-shell"]'));
  await page.waitForTimeout(300);
  assert.ok(requests.some(url => url.endsWith('/studio/nodes/standby')), 'Node ID selects the remote host even when ports match');
  await page.evaluate(() => { window.location.hash = 'view=sql'; });
  await page.locator('.database-studio .cm-editor').first().waitFor();
  await page.getByRole('button', { name: /全屏$/ }).click();
  assert.equal(await page.locator('.database-studio-fullscreen').count(), 1);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.database-studio-fullscreen').count(), 0);
  await page.screenshot({ path: '/tmp/product-platform-studio-cman.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No mobile page overflow');
  assert.deepEqual(errors, []);
  console.log('Studio workspace checks passed: single UI, four palettes, node identity, SQL, fullscreen and mobile');
} finally {
  await browser.close();
}
