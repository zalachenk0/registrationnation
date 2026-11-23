// ============================================================================
// File:         staff-panel_CG.test.mjs
// Written by:   CG (Cristian Gonzalez)
// What it does: Tests the Nov 23 changes: the sign-out bar is gone, the hidden
//               staff panel only opens with Ctrl+Shift+L from a real keyboard,
//               a print that fails after payment shows its reason instead of
//               "printing", and Review is back to typed initials.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  Node's built-in test runner; TypeScript (to load src/ files);
//               React and jsdom (a browser page in Node).
// ============================================================================
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test, after } from 'node:test';
import ts from 'typescript';
import React, { act } from 'react';
import { JSDOM } from 'jsdom';

const transpile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const load = text => import('data:text/javascript;base64,' + Buffer.from(text).toString('base64'));
const source = name => readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');
globalThis.__spReact = React;
const reactLine = /import \{ ([^}]+) \} from "react";/;
const withReact = text => text.replace(reactLine, (_, names) => `const React = globalThis.__spReact; const { ${names.replace(/type \w+,?\s*/g, '')} } = React;`);

const { StaffPanel, isStaffShortcut } = await load(transpile(withReact(source('StaffPanel_CG.tsx'))));
const { submitPrint } = await load(transpile(source('integration_BV.ts')));
const originalFetch = globalThis.fetch;
after(() => { globalThis.fetch = originalFetch; });

async function page(t) {
  const dom = new JSDOM('<!doctype html><body><div id="root"></div></body>', { url: 'http://127.0.0.1:5000' });
  const saved = {};
  for (const name of ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']) saved[name] = globalThis[name];
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true });
  const { createRoot } = await import('react-dom/client');
  const root = createRoot(document.getElementById('root'));
  t.after(async () => { await act(async () => root.unmount()); Object.assign(globalThis, saved); dom.window.close(); });
  const press = (init) => act(async () => dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })));
  return { dom, root, press, panel: () => document.querySelector('[data-staff-panel]') };
}

const staff = { username: 'front-desk', mode: 'staff' };

// ---- the shortcut ----

test('only Ctrl+Shift+L counts, from the physical L key, and holding it down does not flicker the panel', () => {
  const key = (extra) => ({ ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, code: 'KeyL', ...extra });
  assert.equal(isStaffShortcut(key({ ctrlKey: true, shiftKey: true })), true);
  assert.equal(isStaffShortcut(key({})), false, 'plain L (on-screen keyboard typing)');
  assert.equal(isStaffShortcut(key({ shiftKey: true })), false, 'capital L');
  assert.equal(isStaffShortcut(key({ ctrlKey: true })), false, 'Ctrl+L is the browser address bar');
  assert.equal(isStaffShortcut(key({ ctrlKey: true, shiftKey: true, altKey: true })), false);
  assert.equal(isStaffShortcut(key({ ctrlKey: true, shiftKey: true, metaKey: true })), false);
  assert.equal(isStaffShortcut(key({ ctrlKey: true, shiftKey: true, code: 'KeyK' })), false);
  assert.equal(isStaffShortcut(key({ ctrlKey: true, shiftKey: true, repeat: true })), false);
});

// ---- the panel ----

test('the panel is invisible until Ctrl+Shift+L, then shows who is signed in; Esc closes it', async t => {
  const p = await page(t);
  await act(async () => p.root.render(React.createElement(StaffPanel, { staff, onSignOut: async () => '' })));
  assert.equal(p.panel(), null, 'nothing a customer can see or tap');
  assert.equal(document.body.textContent.includes('Sign out'), false);
  await p.press({ key: 'l', code: 'KeyL' });
  await p.press({ key: 'L', code: 'KeyL', shiftKey: true });
  assert.equal(p.panel(), null, 'typing l or L does nothing');
  await p.press({ key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true });
  assert.ok(p.panel());
  assert.match(p.panel().textContent, /Signed in as front-desk \(Staff\)/);
  await p.press({ key: 'Escape', code: 'Escape' });
  assert.equal(p.panel(), null);
  await p.press({ key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true });
  await p.press({ key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true });
  assert.equal(p.panel(), null, 'the shortcut toggles it closed too');
});

test('Sign out calls the sign-out and closes; a refusal stays on the panel with its reason', async t => {
  const p = await page(t);
  let answer = 'Complete the current payment and receipt, then start a new renewal before signing out.';
  let calls = 0;
  await act(async () => p.root.render(React.createElement(StaffPanel, { staff, onSignOut: async () => { calls++; return answer; } })));
  await p.press({ key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true });
  const button = () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Sign out');
  await act(async () => button().click());
  assert.equal(calls, 1);
  assert.ok(p.panel(), 'still open');
  assert.match(p.panel().querySelector('[role=alert]').textContent, /Complete the current payment/);
  answer = '';
  await act(async () => button().click());
  assert.equal(calls, 2);
  assert.equal(p.panel(), null, 'signed out, panel gone');
});

test('the panel closes itself after a minute so the next customer never sees it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const p = await page(t);
  await act(async () => p.root.render(React.createElement(StaffPanel, { staff, onSignOut: async () => '' })));
  await p.press({ key: 'L', code: 'KeyL', ctrlKey: true, shiftKey: true });
  await act(async () => t.mock.timers.tick(59000));
  assert.ok(p.panel());
  await act(async () => t.mock.timers.tick(1500));
  assert.equal(p.panel(), null);
});

// ---- the form uses the panel, not the old bar ----

test('the form has no visible sign-out bar anymore and puts the hidden panel on every screen', () => {
  const app = source('App_BV_CG.tsx');
  assert.ok(!app.includes('staffBar = '), 'old bar is gone');
  assert.ok(!app.includes('Master administrator'), 'the bar\'s staff name line is gone');
  assert.ok(app.includes('staffBar={staffPanel}'), 'start screen');
  assert.equal(app.split('{staffPanel}{/* [CG] */}').length - 1, 2, 'print screen and form');
});

test('the built form in dist/ has the panel and the print-failed message', () => {
  const index = readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8');
  const bundle = readFileSync(new URL('../dist/' + index.match(/src="\/(assets\/index-[^"]+\.js)"/)[1], import.meta.url), 'utf8');
  for (const piece of ['KeyL', 'data-staff-panel', "the receipt didn't print", 'Tap Retry Printing below']) {
    assert.ok(bundle.includes(piece), 'missing from the build: ' + piece);
  }
  assert.ok(!bundle.includes(' · Master administrator'), 'old bar still in the build');
});

// ---- print failing after payment ----

test('a print error page that is not JSON still gives the screen a reason it can show', async () => {
  globalThis.fetch = async () => new Response('<!doctype html><title>500 Internal Server Error</title>', { status: 500, headers: { 'Content-Type': 'text/html' } });
  await assert.rejects(submitPrint({ confirmation_number: 'RN1-20261123-201500-A1B2C3' }), /problem making the receipt \(error 500\)/);
  globalThis.fetch = async () => new Response(JSON.stringify({ error: 'CUPS rejected the job.' }), { status: 503 });
  await assert.rejects(submitPrint({}), /CUPS rejected the job/);
});

test('when the card was charged but printing failed, the payment panel says so instead of "printing"', async t => {
  const p = await page(t);
  const text = withReact(source('TerminalPayment_CG.tsx'))
    .replace(/import \{ PAYMENT_LABEL[^;]+;/, 'const PAYMENT_LABEL = "$114.25";')
    .replace(/import \{ mountTerminalEntry \}[^;]+;/, 'const mountTerminalEntry = () => new Promise(() => {});');
  const { TerminalPayment } = await load(transpile(text));
  const show = props => act(async () => p.root.render(React.createElement(TerminalPayment,
    { controllerRef: { current: null }, onReady: () => {}, status: '', busy: false, error: '', paid: true, ...props })));
  await show({});
  assert.match(document.body.textContent, /Payment approved/);
  assert.match(document.body.textContent, /are printing/);
  await show({ error: 'Your payment went through, so do not pay again. CUPS rejected the job.' });
  assert.match(document.body.textContent, /Your payment went through, but the receipt didn't print/);
  assert.match(document.body.textContent, /CUPS rejected the job\. Tap Retry Printing below, or get a staff member\./);
  assert.doesNotMatch(document.body.textContent, /are printing/);
  await show({ error: 'Your payment went through, so do not pay again. CUPS rejected the job.', busy: true });
  assert.doesNotMatch(document.body.textContent, /didn't print/, 'a retry in progress is not a failure');
});

// ---- typed initials instead of the finger signature ----

test('Review asks for typed initials and the print sends initials, not a signature picture', () => {
  const app = source('App_BV_CG.tsx');
  assert.ok(!app.includes('SignaturePad'), 'the signature pad is off the form');
  assert.ok(app.includes('Enter your initials to sign'));
  assert.match(app, /setInitials\(event\.target\.value\.replace\(\/\[\^a-z\]\/gi, ""\)\.toUpperCase\(\)\)/, 'letters only, upper case, like printing_BV.py wants');
  assert.match(app, /maxLength=\{8\}/);
  assert.match(app, /\n\s+initials,\n/, 'initials go in the print request');
  assert.ok(!/\n\s+signature,\n/.test(app), 'no signature in the print request');
  const index = readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8');
  const bundle = readFileSync(new URL('../dist/' + index.match(/src="\/(assets\/index-[^"]+\.js)"/)[1], import.meta.url), 'utf8');
  assert.ok(bundle.includes('Enter your initials to sign') && !bundle.includes('Sign here with your finger'));
});
