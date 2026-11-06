// ============================================================================
// File:         reader-only_CG.test.mjs
// Written by:   CG (Cristian Gonzalez)
// What it does: Tests dist/reader-only_CG.js, which makes today's compiled
//               kiosk form card-reader only: the "enter the card manually"
//               button is hidden and the messages that mention it are fixed.
//               Also checks every text it looks for really is in the compiled
//               form, so a future build with different wording is caught.
// Team-written: Logic written by CG (Cristian Gonzalez).
// Third-party:  Node's built-in test runner; jsdom (a browser page in Node).
// ============================================================================
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const dist = (name) => new URL('../dist/' + name, import.meta.url);
const code = readFileSync(dist('reader-only_CG.js'), 'utf8');
const index = readFileSync(dist('index.html'), 'utf8');
const bundleName = index.match(/src="\/(assets\/index-[^"]+\.js)"/)[1];
const bundle = readFileSync(dist(bundleName), 'utf8');

const MANUAL_BUTTON = 'Reader not working? Enter the card manually instead';
const TOO_SLOW = 'The card reader is taking too long to respond. Check that it is on and on the same network as this kiosk, then retry, or enter the card manually.';
const CANCELED = 'Reader payment canceled. Try the reader again or enter the card manually.';
const TRY_AGAIN = ' Try again, or enter the card manually.';
const ANOTHER_CARD = ' Try another card, or enter the card manually.';

// a page shaped like the compiled form's card reader step, with the script loaded first, like dist/index.html does
function page(body = '') {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', { runScripts: 'outside-only' });
  dom.window.eval(code);
  dom.window.document.getElementById('root').innerHTML = body;
  return dom.window;
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0)); // let the MutationObserver run

const READER_STEP = `
  <p role="status">Card reader connected and ready</p>
  <p role="status" aria-live="polite" id="status"></p>
  <button type="button" id="cancel">Cancel card reader payment</button>
  <button type="button" id="manual" style="border:1px solid">${MANUAL_BUTTON}</button>
  <div role="alert"><p id="error"></p><button type="button" id="retry">Retry connecting</button></div>`;

test('every text the script looks for is really in the compiled kiosk form', () => {
  for (const text of [MANUAL_BUTTON, TOO_SLOW, CANCELED, TRY_AGAIN, ANOTHER_CARD]) {
    assert.ok(bundle.includes(text), 'not found in ' + bundleName + ': ' + text);
  }
});

test('dist/index.html loads the stand-in and this script before the compiled form', () => {
  const at = (needle) => index.indexOf(needle);
  assert.ok(at('/stripe-terminal-server_CG.js') > 0 && at('/reader-only_CG.js') > 0);
  assert.ok(at('/reader-only_CG.js') < at('/' + bundleName) && at('/stripe-terminal-server_CG.js') < at('/' + bundleName));
});

test('the manual button is hidden and can\'t be pressed; the other buttons are untouched', async () => {
  const window = page(READER_STEP);
  await settle();
  const $ = (id) => window.document.getElementById(id);
  assert.equal($('manual').hidden, true);
  assert.equal($('manual').style.display, 'none');
  assert.equal($('manual').disabled, true);
  assert.equal($('manual').style.border, '1px solid'); // React's own styles are left alone
  for (const id of ['cancel', 'retry']) {
    assert.equal($(id).hidden, false);
    assert.equal($(id).style.display, '');
  }
});

test('a manual button that appears later (the form moves to the payment step) is hidden too', async () => {
  const window = page('<p>Step 4</p>');
  await settle();
  window.document.getElementById('root').innerHTML = READER_STEP;
  await settle();
  assert.equal(window.document.getElementById('manual').hidden, true);
});

test('each message is rewritten without the typed card', () => {
  const { fix } = page().kioskReaderOnly;
  assert.equal(fix(TOO_SLOW), 'The card reader is taking too long to respond. Check that it is on and connected to the internet, then retry.');
  assert.equal(fix(CANCELED), 'Reader payment canceled. Press Pay to try the reader again.');
  assert.equal(fix('No card was read within 5 minutes, so the reader was cleared.' + TRY_AGAIN),
    'No card was read within 5 minutes, so the reader was cleared. Press Pay to try again.');
  assert.equal(fix('Your card was declined.' + ANOTHER_CARD), 'Your card was declined. Try another card.');
  // the stand-in's own message already says to press Pay, so it isn't said twice
  assert.equal(fix('network blip Press Pay again to check this same payment.' + TRY_AGAIN), 'network blip Press Pay again to check this same payment.');
});

test('messages the kiosk shows on other steps are never changed', () => {
  const { fix } = page().kioskReaderOnly;
  for (const text of ['Scan timed out. Wait for processing to finish, then retry or enter the fields manually.',
    'No readable fields found. Reposition the document and try again, or enter the fields manually.',
    'Enter manually', 'Card reader connected and ready', TRY_AGAIN, '']) {
    assert.equal(fix(text), null, text);
  }
});

test('a message React writes into the page, then changes, is fixed each time', async () => {
  const window = page(READER_STEP);
  await settle();
  const error = window.document.getElementById('error');
  error.textContent = TOO_SLOW; // a new text node, like React's first render of the message
  await settle();
  assert.ok(error.textContent.endsWith('connected to the internet, then retry.'));
  error.firstChild.data = CANCELED; // React updating the same text node in place
  await settle();
  assert.equal(error.textContent, 'Reader payment canceled. Press Pay to try the reader again.');
  error.firstChild.data = 'Card reader not connected'; // ordinary text goes through unchanged
  await settle();
  assert.equal(error.textContent, 'Card reader not connected');
});
