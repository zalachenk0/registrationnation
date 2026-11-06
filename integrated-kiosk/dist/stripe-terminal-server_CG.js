// ============================================================================
// File:         stripe-terminal-server_CG.js
// Written by:   CG (Cristian Gonzalez)
// What it does: Lets today's built kiosk screen (dist/) take card-reader
//               payments the server-driven way, without rebuilding dist/.
//               The built screen asks the Stripe Terminal JS SDK for the
//               reader. Its loader uses window.StripeTerminal if it already
//               exists, so this file provides one: the same six calls the
//               screen makes, done through our server's /api/payment/reader_*
//               routes. Stripe then carries the payment to the reader over the
//               internet, so the kiosk and the reader never have to reach each
//               other on the local network (any Wi-Fi works).
//               Loaded by dist/index.html before the screen's own script.
//               Not needed once src/terminal_CG.ts (already server-driven) is
//               built into dist/; a fresh build simply leaves this file out.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  Copies the shape of the Stripe Terminal JS SDK calls
//               (stripe.com/docs/terminal/js) that the built screen uses; no
//               Stripe code is included here.
// ============================================================================
(function () {
  "use strict";
  if (window.StripeTerminal) return; // something real is already here, so stay out of the way

  // same as the built screen's own /api/ calls: the staff-login guard needs the X-Kiosk-Request header and the session
  // cookie on every call, the payment routes need X-Kiosk-Payment, and a 401 sends the screen back to sign-in
  async function call(path, body) {
    const response = await fetch("/api/payment/" + path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Kiosk-Payment": "1", "X-Kiosk-Request": "1" },
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 401) window.dispatchEvent(new window.Event("kiosk-session-expired"));
    let data = {};
    try { data = await response.json(); } catch (error) { /* a non-JSON reply falls through to the generic message */ }
    if (!response.ok) throw new Error(data.error || "Payment service unavailable.");
    return data;
  }
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const failed = (message) => ({ error: { message: message } }); // the SDK's way of saying something went wrong

  const WAIT_LIMIT_MS = 5 * 60 * 1000; // a reader left asking for a card this long is cleared
  const CHECK_EVERY_MS = 1000;
  const NETWORK_TRIES = 5; // brief hiccups are retried; checking again is always safe, it can't charge twice

  function create() {
    let current = null; // the payment the reader is asking for right now, if any

    // the reader has the payment: check about once a second until it's approved, declined or canceled
    async function waitForCard(job) {
      const started = Date.now();
      let misses = 0;
      for (;;) {
        let answer;
        try {
          answer = await call("reader_status", { payment_intent: job.intent });
          misses = 0;
        } catch (error) {
          if (++misses >= NETWORK_TRIES) return failed(error.message + " Press Pay again to check this same payment.");
          await pause(CHECK_EVERY_MS);
          continue;
        }
        if (answer.reader === "approved") return { paymentIntent: { id: job.intent, status: "requires_capture" } };
        if (job.stopped) return failed("Reader payment canceled.");
        if (answer.reader === "failed") return failed(answer.readerMessage || "The card was declined.");
        if (answer.reader === "idle") return failed(answer.readerMessage || "The payment was canceled on the reader.");
        if (Date.now() - started > WAIT_LIMIT_MS) {
          await call("reader_cancel", { payment_intent: job.intent }).catch(() => undefined);
          return failed("No card was read within 5 minutes, so the reader was cleared.");
        }
        await pause(CHECK_EVERY_MS);
      }
    }

    return {
      // there's nothing to search for on the network: the server already knows which reader is this kiosk's
      async discoverReaders() {
        try {
          const settings = (await call("config")).serverReader;
          if (!settings || !settings.enabled) return failed((settings && settings.problem) || "The card reader is not set up on this kiosk.");
          return { discoveredReaders: [{ id: "server-driven", label: "Card reader (through Stripe)", simulated: false }] };
        } catch (error) {
          return failed(error.message);
        }
      },
      async connectReader(reader) {
        return { reader: reader };
      },
      // [CG] only the physical S700 is used now, so there's no simulator to set up. Kept as a do-nothing call
      // because the compiled form still has the (now unused) line that calls it
      setSimulatorConfiguration() {},
      // the screen hands over the payment's client secret; its id is the part before "_secret_"
      async collectPaymentMethod(clientSecret) {
        const job = { intent: String(clientSecret || "").split("_secret_")[0], stopped: false };
        current = job;
        try {
          const answer = await call("reader_start", { payment_intent: job.intent });
          if (answer.reader === "approved") return { paymentIntent: { id: job.intent, status: "requires_capture" } };
          return await waitForCard(job);
        } catch (error) {
          return failed(error.message);
        } finally {
          if (current === job) current = null;
        }
      },
      // server-driven approval already happened on the reader, so there's nothing left to do here; capture comes next
      async processPayment(paymentIntent) {
        return { paymentIntent: Object.assign({}, paymentIntent, { status: "requires_capture" }) };
      },
      // the screen's Cancel: clear the reader. If the card was approved at that same moment, waitForCard still reports it
      async cancelCollectPaymentMethod() {
        const job = current;
        if (!job) return {};
        job.stopped = true;
        await call("reader_cancel", { payment_intent: job.intent }).catch(() => undefined);
        return {};
      },
    };
  }

  window.StripeTerminal = { create: create, serverDriven: true };
})();
