import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source = fs.readFileSync(new URL('../src/platform/stepNormalizer.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { normalizeStep } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
assert.equal(normalizeStep({ status: 'finished', result: 'FAIL' }, 0).status, 'FAIL');
assert.equal(normalizeStep({ status: 'finished', result: 'BLOCKED' }, 0).status, 'BLOCKED');
assert.equal(normalizeStep({ status: 'finished' }, 0).status, 'UNKNOWN');
const negative = normalizeStep({ result: 'PASS', expected: false, actual: false }, 0);
assert.equal(negative.status, 'PASS');
assert.equal(negative.expected, 'false');
assert.equal(normalizeStep({ expected: 0 }, 0).expected, '0');
assert.match(normalizeStep({ expected: [] }, 0).expected, /0 行/);
assert.match(normalizeStep({ expected: [['ACTIVE', 'OK']] }, 0).expected, /ACTIVE/);
for (const [actual, display] of [[0, '0'], [false, 'false'], [[], '0 行（空结果集）'], ['', '""']]) {
  const step = normalizeStep({ intent: 'verify', actual, output: 'raw evidence' }, 0);
  assert.equal(step.actualPayload?.raw, display);
  assert.ok(step.evidenceItems.some((item) => item.value === 'raw evidence'));
}
assert.equal(normalizeStep({ output: 'fallback only' }, 0).actualPayload?.raw, 'fallback only');
console.log('step normalizer regression checks passed');
