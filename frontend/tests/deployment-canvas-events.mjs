import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { renderDeploymentCanvas } from '../src/components/referenceDeploymentCanvas.js';

const id = `node'"&<\\;window.pwned=true;//`;
const html = renderDeploymentCanvas({
  nodes: [{ id, label: id, type: 'db_master', status: 'active', host: 'localhost', port: 7000, x: 0, y: 0 }],
  edges: [], clusters: [], health: { proxy_running: true },
});
assert.doesNotMatch(html, /\bon\w+\s*=|window\.dashboard/);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.setContent(html);
  assert.equal(await page.locator('[data-node-id]').first().getAttribute('data-node-id'), id);
  assert.equal(await page.locator('[data-node-id]').first().getAttribute('data-action'), 'inspect');
  assert.equal(await page.locator('[data-action=sql]').count(), 1);
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  console.log('Canvas renderer checks passed: inert markup, escaped node identity and explicit actions');
} finally {
  await browser.close();
}
