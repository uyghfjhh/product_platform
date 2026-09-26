import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const taskId = process.env.SCENE_TASK_ID;
if (!taskId) throw new Error('SCENE_TASK_ID is required');
const expectedLabel = process.env.SCENE_EXPECTED_LABEL || 'pg_2';
const expectedState = process.env.SCENE_EXPECTED_STATE || 'available';
const expectedObserved = Number(process.env.SCENE_EXPECTED_OBSERVED || 5);
const expectedAll = Number(process.env.SCENE_EXPECTED_ALL || 19);
const base = process.env.PLATFORM_URL || 'http://127.0.0.1:8080';
const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const response = await page.request.get(`${base}/api/v1/operations/${taskId}`);
    assert.equal(response.status(), 200);
    const task = await response.json();
    assert.equal(task.status, 'SUCCEEDED');
    await page.route('**/api/v1/operations?limit=20', (route) => route.fulfill({
      json: [{ ...task, status: 'RUNNING' }],
    }));
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'RUNNING' }).click();
    await page.locator('.scene-replay .react-flow__node').filter({ hasText: expectedLabel }).first().waitFor();
    await page.waitForFunction(() => {
      const box = document.querySelector('.ant-drawer-content-wrapper')?.getBoundingClientRect();
      return Boolean(box && box.left >= -1 && box.right <= innerWidth + 1);
    });
    const scene = await page.locator('.scene-replay').innerText();
    assert.ok(scene.includes(expectedLabel));
    assert.ok(scene.includes(expectedState));
    if (expectedLabel === 'pg_220') {
      assert.match(scene, /write-leader/);
      assert.match(scene, /replica/);
      assert.match(scene, /主节点：pg_220/);
      assert.match(scene, /有效角色：PRIMARY/);
      assert.match(scene, /有效状态：WRITE_ONLY/);
      assert.match(scene, /写集群：mmr_cluster_1/);
      assert.match(scene, /接管集群：mmr_cluster_2/);
    }
    assert.equal(await page.locator('.scene-replay .react-flow__node').count(), expectedObserved);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: `/tmp/product-platform-live-scene-${viewport.width}.png` });
    await page.locator('.scene-caption .ant-segmented-item').filter({ hasText: '全部' }).click();
    assert.equal(await page.locator('.scene-replay .react-flow__node').count(), expectedAll);
    await page.close();
  }
  console.log(`Live task ${taskId}: observed entities rendered on desktop and mobile`);
} finally {
  await browser.close();
}
