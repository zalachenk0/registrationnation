import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/payment.ts', import.meta.url), 'utf8')
  .replace(/import \{ loadStripe, type Stripe, type StripeElements \} from "@stripe\/stripe-js";/, 'const loadStripe = globalThis.__loadTestStripe;');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
let sequence = 0;

async function fixture(t) {
  const oldFetch = globalThis.fetch, oldWindow = globalThis.window;
  globalThis.window = { location: { origin: 'http://127.0.0.1:5000' } };
  t.after(() => { globalThis.fetch = oldFetch; globalThis.window = oldWindow; delete globalThis.__loadTestStripe; });
  const calls = [], bodies = [];
  const intent = { id: 'pi_test', client_secret: 'secret_test', status: 'requires_payment_method', amount: 11425, testMode: true };
  let decline = false, lost = false, invalid = false, challengeError = false;
  const elements = { async submit() { calls.push('submit'); return invalid ? { error: { message: 'Incomplete card' } } : {}; } };
  const stripe = {
    async confirmPayment(options) {
      calls.push('confirm');
      assert.equal(options.clientSecret, intent.client_secret);
      assert.equal(options.elements, elements);
      assert.equal(options.redirect, 'if_required');
      if (decline) { decline = false; return { error: { message: 'Card declined' } }; }
      intent.status = 'requires_capture'; return { paymentIntent: intent };
    },
    async handleNextAction(options) {
      calls.push('authenticate'); assert.equal(options.clientSecret, intent.client_secret);
      if (challengeError) return { error: { message: 'Authentication canceled' } };
      intent.status = 'requires_capture'; return { paymentIntent: intent };
    },
  };
  globalThis.__loadTestStripe = async () => stripe;
  globalThis.fetch = async (url, options) => {
    const path = url.split('/').pop(); calls.push(path);
    if (options.body) bodies.push(JSON.parse(options.body));
    if (path === 'create_intent') return Response.json(intent);
    if (path === 'capture_intent') {
      intent.status = 'succeeded';
      if (lost) { lost = false; throw Error('Lost capture response'); }
      return Response.json(intent);
    }
    throw Error('Unexpected endpoint ' + path);
  };
  const module = await import('data:text/javascript;base64,' + Buffer.from(compiled + `\n// instance ${sequence++}`).toString('base64'));
  const payload = { confirmation_number: 'REG-TEST' };
  return { ...module, calls, bodies, intent, pay: () => module.chargeCard(stripe, elements, payload, () => {}),
    decline() { decline = true; }, loseCapture() { lost = true; }, incomplete() { invalid = true; }, cancelChallenge() { challengeError = true; } };
}

test('typed card confirms then captures; print retry never reconfirms or recollects', async t => {
  const f = await fixture(t);
  assert.equal((await f.pay()).status, 'succeeded');
  assert.deepEqual(f.calls, ['create_intent', 'submit', 'confirm', 'capture_intent']);
  await f.pay();
  assert.equal(f.calls.at(-1), 'create_intent');
  assert.equal(f.calls.filter(x => x === 'confirm').length, 1);
  assert.ok(f.bodies.every(body => body.confirmation_number === 'REG-TEST'));
});

test('incomplete and declined cards never capture', async t => {
  const f = await fixture(t);
  f.decline(); await assert.rejects(f.pay(), /Card declined/);
  assert.ok(!f.calls.includes('capture_intent'));
  await f.pay(); assert.equal(f.intent.status, 'succeeded');
  f.intent.status = 'requires_payment_method'; f.incomplete();
  const before = f.calls.filter(x => x === 'confirm').length;
  await assert.rejects(f.pay(), /Incomplete card/);
  assert.equal(f.calls.filter(x => x === 'confirm').length, before);
});

test('lost capture response recovers the same payment without another charge', async t => {
  const f = await fixture(t); f.loseCapture();
  await assert.rejects(f.pay(), /Lost capture/);
  assert.equal((await f.pay()).status, 'succeeded');
  assert.equal(f.calls.filter(x => x === 'confirm').length, 1);
  assert.equal(f.calls.filter(x => x === 'capture_intent').length, 1);
});

test('authorized payments skip card fields and capture directly', async t => {
  const f = await fixture(t); f.intent.status = 'requires_capture';
  await f.pay(); assert.deepEqual(f.calls, ['create_intent', 'capture_intent']);
});

test('3-D Secure recovery authenticates the original intent', async t => {
  const f = await fixture(t); f.intent.status = 'requires_action';
  await f.pay(); assert.deepEqual(f.calls, ['create_intent', 'authenticate', 'capture_intent']);
});

test('canceled authentication and pending payments cannot print or capture', async t => {
  const f = await fixture(t); f.intent.status = 'requires_action'; f.cancelChallenge();
  await assert.rejects(f.pay(), /Authentication canceled/);
  for (const status of ['processing', 'canceled']) {
    f.intent.status = status; await assert.rejects(f.pay(), /Payment status/);
  }
  assert.ok(!f.calls.includes('capture_intent'));
  assert.ok(!f.calls.includes('confirm'));
});
