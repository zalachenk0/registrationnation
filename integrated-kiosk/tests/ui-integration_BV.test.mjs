import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test, after } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/integration_BV.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { fillBlank, requestScan, submitPrint } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const originalFetch = globalThis.fetch;
after(() => { globalThis.fetch = originalFetch; });

test('rescanning fills blanks without replacing manual corrections or adding missing values', () => {
  const current = { name: 'Manually corrected', city: '', vin: '' };
  assert.deepEqual(fillBlank(current, { name: 'OCR guess', city: ' Austin ', vin: undefined }), { name: 'Manually corrected', city: 'Austin', vin: '' });
  assert.equal(current.city, '');
});

test('partial scans remain editable, while empty results and backend failures are rejected', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/camera/scan/license');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.get('X-Kiosk-Request'), '1');
    return Response.json({ success: true, fields: { customer_first_name: 'Jane' } });
  };
  assert.equal((await requestScan('license', new AbortController().signal)).fields.customer_first_name, 'Jane');
  globalThis.fetch = async () => Response.json({ success: true, fields: {} });
  await assert.rejects(requestScan('insurance', new AbortController().signal), /No readable fields/);
  globalThis.fetch = async () => Response.json({ error: 'Camera unavailable' }, { status: 503 });
  await assert.rejects(requestScan('license', new AbortController().signal), /Camera unavailable/);
});

test('printing propagates errors and retries preserve the submitted reference', async () => {
  const payload = { confirmation_number: 'REG-TEST1234', registrant: 'Jane Sample' };
  const bodies = [];
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/print/registration');
    bodies.push(JSON.parse(options.body));
    return bodies.length === 1 ? Response.json({ error: 'No printer configured' }, { status: 503 }) : Response.json({ success: true });
  };
  await assert.rejects(submitPrint(payload), /No printer configured/);
  await submitPrint(payload);
  assert.deepEqual(bodies, [payload, payload]);
});
