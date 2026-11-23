// ============================================================================
// File:         form-fixes_CG.test.mjs
// Written by:   CG (Cristian Gonzalez)
// What it does: Tests the browser side of the Nov 19 fixes: the shorter
//               transaction ID, the per-step Continue checks, the calls
//               brought back from the earlier compiled form (license front
//               photo, security photo, sign-out), the touch keyboard, and that
//               the built form in dist/ really has all of them.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  Node's built-in test runner; TypeScript (to load src/ files);
//               React and jsdom (a browser page in Node).
// ============================================================================
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { test, after } from 'node:test';
import ts from 'typescript';
import React, { act } from 'react';
import { JSDOM } from 'jsdom';

const transpile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const load = text => import('data:text/javascript;base64,' + Buffer.from(text).toString('base64'));
const source = name => readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');

const { transactionId } = await load(transpile(source('transaction_CG.ts')));
const { stepError, firstBlank, renewalError } = await load(transpile(source('renewal_BV.ts')));
const { saveLicenseFront, requestSecurityPhoto, staffSession, signOut } = await load(transpile(source('integration_BV.ts')));
const originalFetch = globalThis.fetch;
after(() => { globalThis.fetch = originalFetch; });

// ---- transaction ID ----

test('the transaction ID is kiosk, UTC date and time, then 6 random hex digits, and fits the receipt', () => {
  const id = transactionId(new Date('2026-11-19T07:55:12.345Z'));
  assert.match(id, /^RN1-20261119-075512-[0-9A-F]{6}$/);
  assert.equal(id.length, 26);
  assert.match(transactionId(new Date('2026-11-19T07:55:12Z'), 3), /^RN3-/);
  assert.ok(/^[A-Za-z0-9-]{8,64}$/.test(id), 'printing_BV.py and payment_service_CG.py accept it');
  const ids = new Set(Array.from({ length: 200 }, () => transactionId(new Date('2026-11-19T07:55:12Z'))));
  assert.ok(ids.size > 190, 'same second, still different IDs');
});

test('the receipt and this file agree on the ID format', () => {
  const receipt = readFileSync(new URL('../receipt_documents_BV.py', import.meta.url), 'utf8');
  assert.ok(receipt.includes("r'RN(\\d{1,3})-(\\d{8})-(\\d{6})-[0-9A-F]{6}'"));
});

// ---- Continue checks each step ----

const personal = { firstName: 'Jane', lastName: 'Sample', dateOfBirth: '1990-05-13', email: 'jane@example.com',
  phone: '830-555-0123', address: '100 Main St', city: 'New Braunfels', state: 'Texas', zip: '78130', county: 'Comal' };
const license = { licenseNumber: '12345678', licenseState: 'Texas', expirationDate: '2030-12-31', licenseClass: 'C',
  vehicleYear: '2019', vehicleMake: 'Honda', vehicleModel: 'Accord', licensePlate: 'ABC123', vin: '1HGCM82633A004352' };
const insurance = { provider: 'Example', policyNumber: 'ABC123', policyHolder: 'Sample', endDate: '2027-12-31', startDate: '', coverageType: '' };
const empty = obj => Object.fromEntries(Object.keys(obj).map(key => [key, '']));

test('step 1 stops on its own blanks and bad values, but not on later steps\' blanks', () => {
  assert.equal(stepError(1, personal, empty(license), empty(insurance)), '');
  assert.equal(firstBlank(1, { ...personal, county: '' }, license, insurance), 'county');
  assert.match(stepError(1, { ...personal, email: '' }, license, insurance), /marked \*/);
  assert.match(stepError(1, { ...personal, phone: '555-0123' }, license, insurance), /10-digit/);
  assert.match(stepError(1, { ...personal, zip: '7813' }, license, insurance), /ZIP/);
  assert.match(stepError(1, { ...personal, dateOfBirth: '1990-02-30' }, license, insurance), /valid dates/);
});

test('step 2 checks the license fields; step 3 the vehicle and insurance', () => {
  assert.equal(stepError(2, empty(personal), license, empty(insurance)), '');
  assert.equal(firstBlank(2, personal, { ...license, licenseClass: '' }, insurance), 'licenseClass');
  assert.match(stepError(2, personal, { ...license, expirationDate: '2030-13-01' }, insurance), /valid dates/);
  assert.equal(stepError(3, empty(personal), license, insurance), '');
  assert.equal(firstBlank(3, personal, { ...license, vin: '' }, insurance), 'vin');
  assert.match(stepError(3, personal, { ...license, vin: 'SHORT' }, insurance), /17-character/);
  assert.match(stepError(3, personal, license, { ...insurance, startDate: '2028-01-01' }), /end date/);
});

test('the full check before paying is unchanged', () => {
  assert.equal(renewalError(personal, license, insurance), '');
  assert.match(renewalError(personal, license, { ...insurance, endDate: '' }), /required/);
  assert.match(renewalError(personal, { ...license, vin: 'BAD' }, insurance), /17-character/);
});

// ---- calls brought back from the earlier compiled form ----

test('the license front photo is saved through its own route, and a failure says why', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options.method]); return Response.json({ saved: true }); };
  await saveLicenseFront(new AbortController().signal);
  assert.deepEqual(calls, [['/api/camera/license-front', 'POST']]);
  globalThis.fetch = async () => Response.json({ error: 'A scan is still processing.' }, { status: 409 });
  await assert.rejects(saveLicenseFront(new AbortController().signal), /still processing/);
  globalThis.fetch = async () => Response.json({ saved: false });
  await assert.rejects(saveLicenseFront(new AbortController().signal), /could not be saved/);
});

test('the security photo request has its header, never throws, and only one runs at a time', async () => {
  let calls = 0, release;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url, '/api/security/front-capture');
    assert.equal(options.headers.get('X-Kiosk-Security'), '1');
    await new Promise(resolve => { release = resolve; });
    return Response.json({ queued: true });
  };
  const first = requestSecurityPhoto();
  await requestSecurityPhoto();  // a second tap while the first is still out
  release(); await first;
  assert.equal(calls, 1);
  globalThis.fetch = async () => { throw new TypeError('network down'); };
  const warn = console.warn; console.warn = () => {};
  try { await requestSecurityPhoto(); } finally { console.warn = warn; }  // must not throw
});

test('staff session and sign-out', async () => {
  globalThis.fetch = async url => url === '/api/auth/session' ? Response.json({ user: { username: 'staff1', mode: 'staff' } }) : Response.json({}, { status: 500 });
  assert.deepEqual(await staffSession(), { username: 'staff1', mode: 'staff' });
  await assert.rejects(signOut(), /Sign-out failed/);
  globalThis.fetch = async () => Response.json({ error: 'no' }, { status: 401 });
  assert.equal(await staffSession(), null);
});

// ---- the touch keyboard ----

async function keyboardPage(t, html) {
  const dom = new JSDOM(`<!doctype html><body><div id="root">${html}</div><div id="kb"></div></body>`, { url: 'http://127.0.0.1:5000' });
  const saved = {};
  for (const name of ['window', 'document', 'HTMLInputElement', 'HTMLTextAreaElement', 'Node', 'MutationObserver', 'Event', 'ResizeObserver']) saved[name] = globalThis[name];
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLInputElement: dom.window.HTMLInputElement,
    HTMLTextAreaElement: dom.window.HTMLTextAreaElement, Node: dom.window.Node, MutationObserver: dom.window.MutationObserver,
    Event: dom.window.Event, ResizeObserver: class { observe() {} disconnect() {} }, IS_REACT_ACT_ENVIRONMENT: true });
  globalThis.__kbReact = React;
  const { TouchKeyboard } = await load(transpile(source('TouchKeyboard_BV_CG.tsx')
    .replace('import { useEffect, useRef, useState } from "react";', 'const React = globalThis.__kbReact; const { useEffect, useRef, useState } = React;')));
  const { createRoot } = await import('react-dom/client');
  const root = createRoot(document.getElementById('kb'));
  await act(async () => root.render(React.createElement(TouchKeyboard)));
  t.after(async () => { await act(async () => root.unmount()); Object.assign(globalThis, saved); dom.window.close(); });
  const fire = async (target, type) => act(async () => target.dispatchEvent(new dom.window.Event(type, { bubbles: true })));
  const tap = async target => { await fire(target, 'pointerdown'); await act(async () => target.focus()); await fire(target, 'click'); };
  const key = label => [...document.querySelectorAll('.touch-key')].find(button => button.textContent === label);
  return { dom, tap, key, fire, keyboard: () => document.querySelector('.touch-keyboard') };
}

test('tapping a field opens the keyboard, and its keys type into the field', async t => {
  const page = await keyboardPage(t, '<input id="email" type="email" maxlength="5"><button id="go">Continue</button>');
  const email = document.getElementById('email');
  let inputs = 0; email.addEventListener('input', () => inputs++);
  await page.tap(email);
  assert.ok(page.keyboard(), 'keyboard is open');
  assert.equal(email.inputMode, 'none', 'the system keyboard stays closed');
  for (const label of ['j', 'a', 'n', 'e', '@', 'x']) await act(async () => page.key(label).click());
  assert.equal(email.value, 'jane@', 'maxlength still applies');
  assert.equal(inputs, 5, 'React sees each key as typing');
  await act(async () => page.key('⌫').click());
  assert.equal(email.value, 'jane');
});

test('number fields get the number pad', async t => {
  const page = await keyboardPage(t, '<input id="date" type="text" data-keypad="numeric"><input id="name" type="text">');
  await page.tap(document.getElementById('date'));
  assert.ok(page.keyboard().classList.contains('touch-keyboard-numeric'));
  await page.tap(document.getElementById('name'));
  assert.ok(!page.keyboard().classList.contains('touch-keyboard-numeric'));
});

test('touching a button does not close the keyboard until the tap is finished', async t => {
  // closing on touch-down shrank the page under the finger, so the tap could miss Continue
  const page = await keyboardPage(t, '<input id="email" type="email"><button id="go">Continue</button>');
  await page.tap(document.getElementById('email'));
  const go = document.getElementById('go');
  await page.fire(go, 'pointerdown');
  await act(async () => go.focus());
  assert.ok(page.keyboard(), 'still open while the finger is down');
  await page.fire(go, 'click');
  assert.equal(page.keyboard(), null, 'closed once the tap is done');
});

// [CG] Nov 19 bugs from the real touchscreen: Shift closed the keyboard, and the phone hyphen threw the cursor off

test('Shift and #+= change the keys but keep the keyboard open', async t => {
  const page = await keyboardPage(t, '<input id="name" type="text">');
  const name = document.getElementById('name');
  await page.tap(name);
  await page.tap(page.key('Shift'));                     // label turns into SHIFT and the letters into capitals
  assert.ok(page.keyboard(), 'still open after Shift');
  await page.tap(page.key('A'));
  assert.equal(name.value, 'A');
  await page.tap(page.key('#+='));
  assert.ok(page.keyboard(), 'still open after #+=');
  await page.tap(page.key('ABC'));
  assert.ok(page.keyboard(), 'still open after ABC');
});

test('Shift stays the same button when its label changes (a swapped-out button made the tap look like it missed)', async t => {
  const page = await keyboardPage(t, '<input id="name" type="text">');
  await page.tap(document.getElementById('name'));
  const shift = page.key('Shift'), symbols = page.key('#+=');
  await act(async () => shift.click());
  assert.equal(page.key('SHIFT'), shift, 'same element, just relabeled');
  await act(async () => symbols.click());
  assert.equal(page.key('ABC'), symbols);
});

test('a tap on a key that got redrawn mid-tap still counts as a keyboard tap', async t => {
  // in Chromium the redraw can land before the tap reaches the page, so the tapped key is already gone from the page
  const page = await keyboardPage(t, '<input id="name" type="text">');
  await page.tap(document.getElementById('name'));
  const key = page.key('q');
  key.addEventListener('click', () => key.remove());
  await act(async () => key.click());
  assert.ok(page.keyboard(), 'still open');
});

// the same phone formatting InputEl does in App_BV_CG.tsx: digits, then dashes after 3 and 6
function formatsLikeThePhoneField(field) {
  field.addEventListener('input', () => {
    const digits = field.value.replace(/\D/g, '').slice(0, 10);
    field.value = [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6)].filter(Boolean).join('-');
  });
}

test('typing a phone number on the number pad comes out in order, dashes and all', async t => {
  const page = await keyboardPage(t, '<input id="phone" type="tel" data-keypad="numeric" maxlength="12">');
  const phone = document.getElementById('phone');
  formatsLikeThePhoneField(phone);
  await page.tap(phone);
  for (const digit of '8305550123') await act(async () => page.key(digit).click());
  assert.equal(phone.value, '830-555-0123');
  assert.equal(phone.selectionStart, 12, 'cursor stays at the end');
});

test('backspace over a dash removes the digit before it instead of getting stuck', async t => {
  const page = await keyboardPage(t, '<input id="phone" type="tel" data-keypad="numeric" maxlength="12">');
  const phone = document.getElementById('phone');
  formatsLikeThePhoneField(phone);
  await page.tap(phone);
  for (const digit of '8305') await act(async () => page.key(digit).click());
  assert.equal(phone.value, '830-5');
  await act(async () => page.key('⌫').click());
  assert.equal(phone.value, '830');
  phone.setSelectionRange(4, 4);
  phone.value = '830-';                                    // cursor sitting right after a dash
  phone.setSelectionRange(4, 4);
  await act(async () => page.key('⌫').click());
  assert.equal(phone.value, '83');
});

test('fixing a digit in the middle keeps the cursor next to it', async t => {
  const page = await keyboardPage(t, '<input id="phone" type="tel" data-keypad="numeric" maxlength="12">');
  const phone = document.getElementById('phone');
  formatsLikeThePhoneField(phone);
  await page.tap(phone);
  for (const digit of '830555012') await act(async () => page.key(digit).click());
  phone.setSelectionRange(2, 2);                           // between 8 3 | 0
  await act(async () => page.key('9').click());
  assert.equal(phone.value, '839-055-5012');
  assert.equal(phone.selectionStart, 3, 'right after the 9, not shoved back by the dash');
});

// ---- the built form in dist/ ----

const index = readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8');
const bundleName = index.match(/src="\/(assets\/index-[^"]+\.js)"/)[1];
const cssName = index.match(/href="\/(assets\/index-[^"]+\.css)"/)[1];
const bundle = readFileSync(new URL('../dist/' + bundleName, import.meta.url), 'utf8');

test('dist/ is built from today\'s src/ and has everything the Nov 17 build lost', () => {
  assert.ok(existsSync(new URL('../dist/' + cssName, import.meta.url)));
  for (const piece of ['/api/camera/license-front', '/api/security/front-capture', '/api/auth/logout', 'security_event_id',
    'touch-keyboard', 'Scan the front of your license', 'Sign here with your finger', 'Sign out', 'reader_start', 'capture_intent']) {
    assert.ok(bundle.includes(piece), 'missing from ' + bundleName + ': ' + piece);
  }
  for (const gone of ['CRM record (Monday.com)', 'Retry saving to CRM', 'Open Scanner<']) {
    assert.ok(!bundle.includes(gone), 'still in ' + bundleName + ': ' + gone);
  }
  assert.ok(!index.includes('_CG.js'), 'the old stand-in scripts are not loaded');
});

// ---- finger-drag scrolling (DragScroll_CG.ts) ----

async function dragPage(t) {
  const dom = new JSDOM(`<!doctype html><body><div id="box" style="overflow-y:auto"><button id="go">Continue</button><input id="name"></div></body>`);
  const saved = { document: globalThis.document, getComputedStyle: globalThis.getComputedStyle };
  globalThis.document = dom.window.document; globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  t.after(() => { Object.assign(globalThis, saved); dom.window.close(); });
  const box = dom.window.document.getElementById('box');
  Object.defineProperty(box, 'scrollHeight', { value: 2000 }); Object.defineProperty(box, 'clientHeight', { value: 500 });
  let top = 300; Object.defineProperty(box, 'scrollTop', { get: () => top, set: v => { top = v; } });
  const { enableDragScroll } = await load(transpile(source('DragScroll_CG.ts')));
  t.after(enableDragScroll(dom.window.document));
  const pointer = (target, type, y, pointerType = 'mouse') => {
    const event = new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, clientY: y, button: 0 });
    Object.defineProperty(event, 'pointerType', { value: pointerType }); Object.defineProperty(event, 'pointerId', { value: 1 });
    target.dispatchEvent(event);
  };
  const drag = (target, from, to, pointerType) => {
    pointer(target, 'pointerdown', from, pointerType);
    for (let y = from; from < to ? y <= to : y >= to; y += from < to ? 5 : -5) pointer(target, 'pointermove', y, pointerType);
    pointer(target, 'pointerup', to, pointerType);
    return target.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  };
  return { doc: dom.window.document, box, drag, get top() { return top; } };
}

test('a finger drag that the browser reports as a mouse scrolls like a phone', async t => {
  const page = await dragPage(t);
  page.drag(page.box, 400, 100);
  assert.equal(page.top, 600, 'finger up 300px: content up 300px, the first 10px included once the drag starts');
  page.drag(page.box, 100, 300);
  assert.equal(page.top, 400, 'finger down: content back down');
});

test('real touch, a tap, and drags in a field are left alone', async t => {
  const page = await dragPage(t);
  page.drag(page.box, 400, 100, 'touch');
  assert.equal(page.top, 300, 'the browser scrolls real touch itself');
  page.drag(page.doc.getElementById('name'), 400, 100);
  assert.equal(page.top, 300, 'dragging in a field selects text there, as usual');
  let pressed = 0; page.doc.getElementById('go').addEventListener('click', () => pressed++);
  page.drag(page.doc.getElementById('go'), 400, 395);
  assert.equal(pressed, 1, 'a small wobble is still a tap');
});

test('a drag that starts on a button scrolls but does not press it', async t => {
  const page = await dragPage(t);
  let pressed = 0; page.doc.getElementById('go').addEventListener('click', () => pressed++);
  page.drag(page.doc.getElementById('go'), 400, 200);
  assert.equal(pressed, 0);
  assert.ok(page.top > 300);
  page.drag(page.doc.getElementById('go'), 400, 400);
  assert.equal(pressed, 1, 'the next real tap works');
});


// ---- [CG] Nov 23: start screen wording and quieter confidence badges ----

test('the start screen asks for a card without naming the amount', () => {
  const start = source('StartScreen_SL.tsx');
  assert.ok(start.includes('A card ready for payment'));
  assert.ok(!/PAYMENT_LABEL|\$\d|114\.25/.test(start), 'no price on the start screen');
  assert.ok(bundle.includes('A card ready for payment') && !bundle.includes('A card to pay'));
});

test('confidence badges only show at 75% or less', () => {
  const app = source('App_BV_CG.tsx');
  assert.match(app, /export const SHOW_AT_OR_BELOW = 0\.75;/);
  assert.match(app, /const shaky = score !== undefined && score <= SHOW_AT_OR_BELOW;/, '75% itself counts as shaky');
  assert.match(app, /\{shaky && \(/, 'no badge at all for sure fields');
  assert.ok(!bundle.includes('Scanned · '), 'the green "Scanned · 95% sure" badge is gone');
  assert.ok(bundle.includes('Please check · scanned '));
});
