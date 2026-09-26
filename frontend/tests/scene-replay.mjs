import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const task = {
  id: 'scene-browser-check', environment_id: 'cman-lab', action: 'tests.fbasecman',
  target: 'rw_toggle.mmr_hint_read', status: 'RUNNING', reason: null,
  created_at: '2026-09-25T12:00:00Z', started_at: '2026-09-25T12:00:01Z',
  finished_at: null, cancel_requested: false, last_sequence: 3,
};
const payloads = [
  ['scene.topology.configured', {
    schema_version: '1.0', product_id: 'fbasecman', environment_id: 'cman-lab',
    entities: [{ id: 'endpoint:cman-lab', label: 'cman-lab', kind: 'endpoint' }],
    relations: [],
  }],
  ['scene.entity.discovered', {
    schema_version: '1.0', id: 'cman:route:mmr_group:pg_2',
    label: 'pg_2', kind: 'fbasecman.route', group: 'mmr_group',
  }],
  ['scene.entity.observed', {
    schema_version: '1.0', entity_id: 'cman:route:mmr_group:pg_2',
    state: 'available', source: 'fbasecman.route',
    observed_at: '2026-09-25T12:00:02Z',
    details: { candidate_node: 'pg_2', route_status: 'AVAILABLE', is_write_target: 'true' },
  }],
  ['scene.entity.discovered', {
    schema_version: '1.0', id: 'cman:monitor:pg_cluster_1:pg_3',
    label: 'pg_3', kind: 'fbasecman.monitor', group: 'pg_cluster_1',
  }],
  ['scene.entity.observed', {
    schema_version: '1.0', entity_id: 'cman:monitor:pg_cluster_1:pg_3',
    state: 'offline', source: 'fbasecman.monitor',
    observed_at: '2026-09-25T12:00:03Z',
    details: { connect_status: 'OFFLINE', effective_status: 'OFFLINE' },
  }],
].map(([event_type, payload], index) => ({
  task_id: task.id, sequence: index + 1, recorded_at: '2026-09-25T12:00:02Z',
  event_type, payload,
}));

const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.route('**/api/v1/operations?limit=20', (route) => route.fulfill({ json: [task] }));
    await page.route(`**/api/v1/operations/${task.id}`, (route) => route.fulfill({ json: task }));
    await page.route(`**/api/v1/operations/${task.id}/events?after=*`, (route) => {
      const after = Number(new URL(route.request().url()).searchParams.get('after') || 0);
      return route.fulfill({ json: payloads.filter((event) => event.sequence > after) });
    });
    await page.route(`**/api/v1/operations/${task.id}/log?*`, (route) => route.fulfill({
      json: { available: false, path: '', lines: [] },
    }));
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'RUNNING' }).click();
    await page.locator('.scene-replay .react-flow__node').nth(1).waitFor();
    await page.waitForFunction(() => {
      const box = document.querySelector('.ant-drawer-content-wrapper')?.getBoundingClientRect();
      return Boolean(box && box.left >= -1 && box.right <= innerWidth + 1);
    });
    const drawer = await page.locator('.ant-drawer-content-wrapper').boundingBox();
    assert.ok(drawer && drawer.x >= -1 && drawer.x + drawer.width <= viewport.width + 1);
    assert.equal(await page.locator('.scene-replay .react-flow__node').count(), 3);
    assert.match(await page.locator('.scene-replay').innerText(), /pg_2.*available/s);
    assert.match(await page.locator('.scene-replay').innerText(), /pg_3.*offline/s);
    const nodeBoxes = await page.locator('.scene-replay .react-flow__node').evaluateAll((nodes) =>
      nodes.map((node) => {
        const box = node.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
      }));
    const canvas = await page.locator('.scene-canvas').boundingBox();
    assert.ok(canvas && nodeBoxes.every((box) => box.left >= canvas.x - 1 &&
      box.right <= canvas.x + canvas.width + 1));
    assert.ok(nodeBoxes[0].bottom <= nodeBoxes[1].top || nodeBoxes[1].bottom <= nodeBoxes[0].top ||
      nodeBoxes[0].right <= nodeBoxes[1].left || nodeBoxes[1].right <= nodeBoxes[0].left);
    if (viewport.width < 600) {
      assert.equal(await page.locator('.scene-replay .react-flow__controls').count(), 0);
    }
    await page.screenshot({ path: `/tmp/product-platform-scene-${viewport.width}.png` });
    await page.getByRole('button', { name: '上一事件' }).click();
    assert.ok(!(await page.locator('.scene-replay').innerText()).includes('pg_3'));
    await page.getByRole('button', { name: '上一事件' }).click();
    assert.equal(await page.locator('.scene-replay .react-flow__node').count(), 2);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('Scene replay passed: dynamic entity, rewind, desktop and mobile');
} finally {
  await browser.close();
}
