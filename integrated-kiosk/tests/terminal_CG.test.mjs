// ============================================================================
// File:         terminal_CG.test.mjs
// Written by:   CG (Cristian Gonzalez)
// What it does: Tests src/terminal_CG.ts, the kiosk screen's server-driven card
//               reader payment (for when dist/ is rebuilt from src/).
// Team-written: Logic written by CG (Cristian Gonzalez).
// Third-party:  Node's built-in test runner; TypeScript (to compile the file);
//               the server is a stand-in.
// ============================================================================
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test, after } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/terminal_CG.ts', import.meta.url), 'utf8')
  .replace(/import \{[^}]*\} from "\.\/payment_CG";/, 'const { api, getPaymentConfig } = globalThis.__payment;');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;

const realTimeout = globalThis.setTimeout;
const realNow = Date.now;
globalThis.setTimeout = (fn) => setImmediate(fn);  // the 1-second checks run instantly here
after(() => { globalThis.setTimeout = realTimeout; Date.now = realNow; });

const FORM = { confirmation_number: 'REG-TERMINAL-0001', registrant: 'Jane Sample' };

// a pretend kiosk server behind api(), scripted like the stand-in tests
function server({ enabled = true, simulated = false, problem = '', start = 'waiting', startStatus = 'requires_payment_method', states = [], tap = 'approved', failures = 0, cancelClears = true } = {}) {
  const calls = [];
  let left = [...states];
  let broken = failures;
  globalThis.__payment = {
    getPaymentConfig: async () => ({ terminal: { enabled: true, simulated, problem: '' }, serverReader: { enabled, simulated, problem } }),
    api: async (path, body) => {
      calls.push({ path, body });
      if (path === 'reader_start') return { id: 'pi_1', status: startStatus, reader: start };
      if (path === 'reader_simulate_tap') return { id: 'pi_1', reader: tap };
      if (path === 'reader_cancel') { if (cancelClears) left = ['idle']; return { reader: 'idle' }; }
      if (path === 'capture_intent') return { id: 'pi_1', status: 'succeeded' };
      if (path === 'reader_status') {
        if (broken > 0) { broken -= 1; throw new Error('network blip'); }
        const state = left.length > 1 ? left.shift() : (left[0] ?? 'waiting');
        return typeof state === 'string' ? { id: 'pi_1', reader: state } : state;
      }
      throw new Error('unexpected ' + path);
    },
  };
  return calls;
}

let loads = 0;
const load = async () => import('data:text/javascript;base64,' + Buffer.from(compiled + '\n// ' + (loads++)).toString('base64'));
const statuses = [];
const onStatus = (text) => statuses.push(text);

test('[CG] physical reader only: start with the full form, wait for the real tap, then capture; no pretend tap even if told "simulated"', async () => {
  const calls = server({ simulated: true, states: ['approved'] });
  const entry = await (await load()).mountTerminalEntry();
  assert.equal(entry.simulated, undefined);
  const result = await entry.pay(FORM, onStatus);
  assert.equal(result.status, 'succeeded');
  assert.deepEqual(calls.map(c => c.path), ['reader_start', 'reader_status', 'capture_intent']);
  assert.deepEqual(calls[0].body, FORM);
});

test('[CG] no message tells the customer to enter the card manually', async () => {
  const { readFileSync } = await import('node:fs');
  for (const file of ['../src/terminal_CG.ts', '../src/TerminalPayment_CG.tsx', '../src/App_BV_CG.tsx', '../src/payment_CG.ts']) {
    const text = readFileSync(new URL(file, import.meta.url), 'utf8');
    assert.ok(!/card manually|typed card|simulate_tap|CardPayment/i.test(text.replace(/no typed-card backup|There's no typed-card backup|there's no typed-card backup|\(no typed card\)/gi, '')), file);
  }
});

test('real reader: waits, then approved; capture only after approval', async () => {
  const calls = server({ states: ['waiting', 'waiting', 'approved'] });
  const result = await (await (await load()).mountTerminalEntry()).pay(FORM, onStatus);
  assert.equal(result.status, 'succeeded');
  assert.deepEqual(calls.map(c => c.path), ['reader_start', 'reader_status', 'reader_status', 'reader_status', 'capture_intent']);
});

test('an already paid transaction is returned straight away, with no reader step and no second capture', async () => {
  const calls = server({ start: 'approved', startStatus: 'succeeded' });
  const result = await (await (await load()).mountTerminalEntry()).pay(FORM, onStatus);
  assert.equal(result.status, 'succeeded');
  assert.deepEqual(calls.map(c => c.path), ['reader_start']);
});

test('a declined card is an error with the reason, and nothing is captured', async () => {
  const calls = server({ states: [{ reader: 'failed', readerMessage: 'Your card was declined.' }] });
  await assert.rejects((await (await load()).mountTerminalEntry()).pay(FORM, onStatus), /Your card was declined\. Try another card/);
  assert.ok(!calls.some(c => c.path === 'capture_intent'));
});

test('Cancel clears the reader with the same form and the payment ends as canceled', async () => {
  const calls = server({ states: ['waiting'] });
  const entry = await (await load()).mountTerminalEntry();
  const paying = entry.pay(FORM, onStatus);
  await new Promise(resolve => setImmediate(resolve));
  entry.cancel();
  await assert.rejects(paying, /canceled/);
  assert.deepEqual(calls.find(c => c.path === 'reader_cancel').body, FORM);
  assert.ok(!calls.some(c => c.path === 'capture_intent'));
});

test('a card approved at the same moment as Cancel is still captured', async () => {
  const calls = server({ states: ['waiting', 'approved'], cancelClears: false });
  const entry = await (await load()).mountTerminalEntry();
  const paying = entry.pay(FORM, onStatus);
  await new Promise(resolve => setImmediate(resolve));
  entry.cancel();
  assert.equal((await paying).status, 'succeeded');
  assert.ok(calls.some(c => c.path === 'capture_intent'));
});

test('without the server-driven reader the panel says why', async () => {
  server({ enabled: false, problem: 'The server-driven reader needs STRIPE_LOCATION_ID=tml_... in .env' });
  await assert.rejects((await load()).mountTerminalEntry(), /STRIPE_LOCATION_ID/);
});

test('network blips are retried, five in a row give up', async () => {
  server({ states: ['approved'], failures: 4 });
  assert.equal((await (await (await load()).mountTerminalEntry()).pay(FORM, onStatus)).status, 'succeeded');
  server({ states: ['approved'], failures: 5 });
  await assert.rejects((await (await load()).mountTerminalEntry()).pay(FORM, onStatus), /network blip/);
});

test('a reader left waiting 5 minutes is cleared', async () => {
  let now = 0;
  Date.now = () => (now += 61000);
  try {
    const calls = server({ states: ['waiting'] });
    await assert.rejects((await (await load()).mountTerminalEntry()).pay(FORM, onStatus), /5 minutes/);
    assert.ok(calls.some(c => c.path === 'reader_cancel'));
  } finally { Date.now = realNow; }
});
