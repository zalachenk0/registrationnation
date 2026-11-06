// ============================================================================
// File:         stripe-terminal-server_CG.test.mjs
// Written by:   CG (Cristian Gonzalez)
// What it does: Tests dist/stripe-terminal-server_CG.js, the stand-in that lets
//               today's built kiosk screen use the server-driven card reader.
//               It is driven in the exact order the built screen calls the
//               Stripe Terminal SDK (same as the old src/terminal_CG.ts).
// Team-written: Logic written by CG (Cristian Gonzalez).
// Third-party:  Node's built-in test runner; the server is a stand-in.
// ============================================================================
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const code = readFileSync(new URL('../dist/stripe-terminal-server_CG.js', import.meta.url), 'utf8');

// a pretend kiosk server: answers the routes the stand-in calls, from a script of reader states
function server({ enabled = true, simulated = false, problem = '', states = [], tap = 'approved', failures = 0, cancelClears = true } = {}) {
  const calls = [];
  let left = [...states];
  let broken = failures;
  const fetch = async (url, options = {}) => {
    const path = url.replace('/api/payment/', '');
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, body, method: options.method, header: options.headers?.['X-Kiosk-Payment'],
                 login: options.headers?.['X-Kiosk-Request'], credentials: options.credentials });
    const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
    if (path === 'config') return reply({ serverReader: { enabled, simulated, problem } });
    if (path === 'reader_start') return reply({ id: body.payment_intent, reader: 'waiting' });
    if (path === 'reader_simulate_tap') return reply({ id: body.payment_intent, reader: tap });
    if (path === 'reader_cancel') { if (cancelClears) left = ['idle']; return reply({ reader: 'idle' }); }
    if (path === 'reader_status') {
      if (broken > 0) { broken -= 1; throw new Error('network blip'); }
      const state = left.length > 1 ? left.shift() : (left[0] ?? 'waiting');
      return reply(typeof state === 'string' ? { reader: state } : state);
    }
    return reply({ error: 'unexpected ' + path }, 404);
  };
  return { fetch, calls };
}

// loads the file the way the browser does, into a fresh "window", with instant timers
function load(fetch, { existing, clock } = {}) {
  const window = existing ? { StripeTerminal: existing } : {};
  const context = vm.createContext({
    window, fetch, setTimeout: (fn) => setImmediate(fn),
    Date: clock ? { now: clock } : Date, Object, String, Error, Promise, JSON,
  });
  vm.runInContext(code, context);
  return window.StripeTerminal;
}

// the built screen's own sequence: create, discover, connect, (simulator card), collect, process
async function payLikeTheBuiltScreen(sdk, { simulated = false, secret = 'pi_screen_1_secret_abc', during } = {}) {
  const terminal = sdk.create({ onFetchConnectionToken: async () => 'unused', onUnexpectedReaderDisconnect: () => {} });
  const found = await terminal.discoverReaders(simulated ? { simulated: true } : { simulated: false, location: 'tml_x' });
  if ('error' in found) return { stage: 'discover', ...found };
  const connected = await terminal.connectReader(found.discoveredReaders[0]);
  if ('error' in connected) return { stage: 'connect', ...connected };
  if (simulated) terminal.setSimulatorConfiguration({ testCardNumber: '4242424242424242' });
  const collecting = terminal.collectPaymentMethod(secret);
  if (during) await during(terminal);
  const collected = await collecting;
  if ('error' in collected) return { stage: 'collect', ...collected };
  const processed = await terminal.processPayment(collected.paymentIntent);
  return { stage: 'done', ...processed };
}

test('[CG] physical reader only: even if the server or the screen says "simulated", no pretend tap is ever sent', async () => {
  const { fetch, calls } = server({ simulated: true, states: ['approved'] });
  const sdk = load(fetch);
  const result = await payLikeTheBuiltScreen(sdk, { simulated: true });  // the compiled form's old simulator branch, worst case
  assert.equal(result.stage, 'done');
  assert.deepEqual({ ...result.paymentIntent }, { id: 'pi_screen_1', status: 'requires_capture' });  // copied out of the test's sandbox
  assert.deepEqual(calls.map(c => c.path), ['config', 'reader_start', 'reader_status']);  // the customer's real tap is what approves it
  assert.deepEqual(calls[1].body, { payment_intent: 'pi_screen_1' });                       // never the secret itself
  const found = await sdk.create({}).discoverReaders({ simulated: true });
  assert.equal(found.discoveredReaders[0].simulated, false);                                // the screen is never told it has a simulator
  assert.ok(calls.every(c => c.header === '1'));                                            // the kiosk payment header on every call
  assert.ok(calls.every(c => c.login === '1' && c.credentials === 'same-origin'));         // and what the staff-login guard needs
  assert.equal(calls[0].method, 'GET');
});

test('a real reader: keeps checking while it waits, then approved, and no pretend tap is ever sent', async () => {
  const { fetch, calls } = server({ states: ['waiting', 'waiting', 'waiting', 'approved'] });
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.equal(result.stage, 'done');
  assert.equal(calls.filter(c => c.path === 'reader_status').length, 4);
  assert.ok(!calls.some(c => c.path === 'reader_simulate_tap'));
});

test('a declined card comes back as the SDK\'s error, with the reader\'s reason and no "cancel" in it', async () => {
  const { fetch } = server({ states: [{ reader: 'failed', readerMessage: 'Your card was declined.' }] });
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.equal(result.stage, 'collect');
  assert.equal(result.error.message, 'Your card was declined.');
});

test('the screen\'s Cancel clears the reader and reports a cancel (the screen shows its "canceled" message)', async () => {
  const { fetch, calls } = server({ states: ['waiting'] });
  const result = await payLikeTheBuiltScreen(load(fetch), { during: (terminal) => terminal.cancelCollectPaymentMethod() });
  assert.equal(result.stage, 'collect');
  assert.match(result.error.message, /cancel/i);
  assert.deepEqual(calls.find(c => c.path === 'reader_cancel').body, { payment_intent: 'pi_screen_1' });
});

test('a card approved at the same moment as Cancel still counts as paid, so it gets captured, not stranded', async () => {
  const { fetch } = server({ states: ['approved'], cancelClears: false });  // the card was approved before the cancel arrived
  const sdk = load(fetch);
  const result = await payLikeTheBuiltScreen(sdk, {
    during: async (terminal) => { await terminal.cancelCollectPaymentMethod(); },
  });
  assert.equal(result.stage, 'done');
});

test('a payment canceled on the reader itself is reported as a cancel', async () => {
  const { fetch } = server({ states: ['idle'] });
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.match(result.error.message, /cancel/i);
});

test('if the server-driven reader is not set up, the reason reaches the screen at the search step', async () => {
  const { fetch, calls } = server({ enabled: false, problem: 'The server-driven reader needs STRIPE_LOCATION_ID=tml_... in .env' });
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.equal(result.stage, 'discover');
  assert.match(result.error.message, /STRIPE_LOCATION_ID/);
  assert.deepEqual(calls.map(c => c.path), ['config']);
});

test('short network blips while waiting are retried; five in a row give up with a safe-to-retry message', async () => {
  let { fetch } = server({ states: ['approved'], failures: 3 });
  assert.equal((await payLikeTheBuiltScreen(load(fetch))).stage, 'done');
  ({ fetch } = server({ states: ['approved'], failures: 5 }));
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.equal(result.stage, 'collect');
  assert.match(result.error.message, /network blip.*same payment/);
});

test('a reader left waiting 5 minutes is cleared', async () => {
  let now = 0;
  const { fetch, calls } = server({ states: ['waiting'] });
  const result = await payLikeTheBuiltScreen(load(fetch, { clock: () => (now += 61000) }));
  assert.match(result.error.message, /5 minutes/);
  assert.ok(calls.some(c => c.path === 'reader_cancel'));
});

test('a server error on start is shown, not thrown', async () => {
  const calls = [];
  const fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('config')) return { ok: true, json: async () => ({ serverReader: { enabled: true, simulated: false } }) };
    return { ok: false, status: 409, json: async () => ({ error: 'The card reader is offline.' }) };
  };
  const result = await payLikeTheBuiltScreen(load(fetch));
  assert.equal(result.stage, 'collect');
  assert.equal(result.error.message, 'The card reader is offline.');
});

test('a signed-out kiosk (401) sends the screen back to sign-in, like the screen\'s own calls do', async () => {
  const events = [];
  const fetch = async () => ({ ok: false, status: 401, json: async () => ({ error: 'Sign in again.' }) });
  const window = { dispatchEvent: (event) => events.push(event.type), Event: class { constructor(type) { this.type = type; } } };
  const context = vm.createContext({ window, fetch, setTimeout: (fn) => setImmediate(fn), Date, Object, String, Error, Promise, JSON });
  vm.runInContext(code, context);
  const result = await payLikeTheBuiltScreen(window.StripeTerminal);
  assert.equal(result.stage, 'discover');
  assert.match(result.error.message, /Sign in again/);
  assert.deepEqual(events, ['kiosk-session-expired']);
});

test('a real StripeTerminal that is already on the page is left alone', () => {
  const real = { create: () => 'real' };
  assert.equal(load(async () => {}, { existing: real }), real);
});
