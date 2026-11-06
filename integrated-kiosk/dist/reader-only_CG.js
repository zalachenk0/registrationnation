// ============================================================================
// File:         reader-only_CG.js
// Written by:   CG (Cristian Gonzalez)
// What it does: Makes today's compiled kiosk form (dist/) card-reader only,
//               without rebuilding dist/. The kiosk has no physical keyboard,
//               and the typed-card form is Stripe's own box, which the kiosk
//               form's on-screen keyboard can't type into. So this file:
//                 - hides the "Reader not working? Enter the card manually
//                   instead" button on the card reader step
//                 - rewrites the reader messages that tell the customer to
//                   enter the card manually
//               The server refuses typed-card payments on its own as well
//               (payment_service_CG.py), so this file only tidies the page.
//               It never removes anything from the page (React still owns
//               those elements), it only hides the button and fixes text.
//               Loaded by dist/index.html. Not needed once src/ (already
//               reader-only) is built into dist/; a fresh build leaves it out.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  Browser MutationObserver API only.
// ============================================================================
(function () {
  "use strict";

  // exact text from the compiled form (tests/reader-only_CG.test.mjs checks each one is really in dist/assets/)
  var MANUAL_BUTTON = "Reader not working? Enter the card manually instead";
  var TOO_SLOW = "The card reader is taking too long to respond. Check that it is on and on the same network as this kiosk, then retry, or enter the card manually.";
  var CANCELED = "Reader payment canceled. Try the reader again or enter the card manually.";
  var TRY_AGAIN = " Try again, or enter the card manually.";
  var ANOTHER_CARD = " Try another card, or enter the card manually.";

  // the compiled form's message -> the reader-only version, or null when it's not one of these
  function fix(text) {
    if (text === TOO_SLOW) return "The card reader is taking too long to respond. Check that it is on and connected to the internet, then retry.";
    if (text === CANCELED) return "Reader payment canceled. Press Pay to try the reader again.";
    if (text.length > TRY_AGAIN.length && text.slice(-TRY_AGAIN.length) === TRY_AGAIN) {
      var reason = text.slice(0, -TRY_AGAIN.length);
      return /press pay/i.test(reason) ? reason : reason + " Press Pay to try again."; // don't say "press Pay" twice
    }
    if (text.length > ANOTHER_CARD.length && text.slice(-ANOTHER_CARD.length) === ANOTHER_CARD) {
      return text.slice(0, -ANOTHER_CARD.length) + " Try another card.";
    }
    return null;
  }

  function fixText(node) {
    var better = fix(node.data);
    if (better !== null) node.data = better; // the new text doesn't match again, so this can't loop
  }

  function hideButton(button) {
    if (button.textContent.trim() !== MANUAL_BUTTON) return;
    button.hidden = true;
    button.style.display = "none";
    button.disabled = true; // even if something showed it again, it can't be pressed
    button.setAttribute("aria-hidden", "true");
  }

  function scan(root) {
    if (root.nodeType === 3) return fixText(root);
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.tagName === "BUTTON") hideButton(root);
    var buttons = root.querySelectorAll ? root.querySelectorAll("button") : [];
    for (var i = 0; i < buttons.length; i++) hideButton(buttons[i]);
    var walker = document.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
    for (var text = walker.nextNode(); text; text = walker.nextNode()) fixText(text);
  }

  var watcher = new MutationObserver(function (changes) {
    for (var i = 0; i < changes.length; i++) {
      var change = changes[i];
      if (change.type === "characterData") {
        fixText(change.target);
        if (change.target.parentNode && change.target.parentNode.tagName === "BUTTON") hideButton(change.target.parentNode);
      }
      for (var j = 0; j < change.addedNodes.length; j++) scan(change.addedNodes[j]);
    }
  });
  watcher.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  if (document.body) scan(document.body);

  window.kioskReaderOnly = { fix: fix }; // for the tests
})();
