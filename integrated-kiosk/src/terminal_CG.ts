// ============================================================================
// File:         terminal_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Browser side card reader payment, the server-driven way: our
//               server asks Stripe to put the payment on the reader, and Stripe
//               carries it to the reader over the internet. The browser never
//               talks to the reader, so any Wi-Fi with internet works (no local
//               DNS setup, no device-to-device traffic).
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  None directly. The Stripe calls happen on the server
//               (payment_service_CG.py) and follow Stripe's server-driven
//               Terminal guide. Until this file is built into dist/, the built
//               screen gets the same behavior from
//               dist/stripe-terminal-server_CG.js.
// ============================================================================
import { api, getPaymentConfig, type CardEntry, type PaymentResult } from "./payment_CG";

const CHECK_EVERY_MS = 1000;            // how often the reader's progress is checked, like reader_test_CG.py
const WAIT_LIMIT_MS = 5 * 60 * 1000;    // a reader left asking for a card this long is cleared
const NETWORK_TRIES = 5;                // brief hiccups are retried; checking again can never charge twice
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// the payment the reader is asking for right now, so Cancel knows what to clear
let waiting: { payload: Record<string, string>; stopped: boolean } | null = null;

async function waitForCard(job: { payload: Record<string, string>; stopped: boolean }, first: PaymentResult): Promise<PaymentResult> {
  const started = Date.now();
  let answer = first;
  let misses = 0;
  for (;;) {
    if (answer.reader === "approved") return answer;
    if (job.stopped) {
      // one last look: a card approved at the same moment as Cancel has to be captured, not left held on the customer's card
      const last: PaymentResult = await api("reader_status", job.payload).catch(() => answer);
      if (last.reader === "approved") return last;
      throw new Error("Reader payment canceled. Press Pay to try the reader again.");
    }
    if (answer.reader === "failed") throw new Error((answer.readerMessage || "The card was declined.") + " Try another card.");
    if (answer.reader === "idle") throw new Error("Reader payment canceled. Press Pay to try the reader again.");
    if (Date.now() - started > WAIT_LIMIT_MS) {
      await api("reader_cancel", job.payload).catch(() => undefined);
      throw new Error("No card was read within 5 minutes, so the reader was cleared. Press Pay to try again.");
    }
    await pause(CHECK_EVERY_MS);
    try {
      answer = await api("reader_status", job.payload);
      misses = 0;
    } catch (error) {
      if (++misses >= NETWORK_TRIES) throw error;
    }
  }
}

// reader flow: the server reuses this transaction's payment (or makes the one and only new one), Stripe shows it on the reader,
// the customer taps, Stripe approves, and our server takes the money
async function collectAndCharge(payload: Record<string, string>, onStatus: (text: string) => void): Promise<PaymentResult> {
  onStatus("Checking payment…");
  const started: PaymentResult = await api("reader_start", payload);
  if (started.status === "succeeded") return started;
  const job = { payload, stopped: false };
  waiting = job;
  try {
    let answer = started;
    if (answer.reader !== "approved") {
      onStatus("Tap, insert, or swipe the card on the reader…");
      answer = await waitForCard(job, answer);
    }
  } finally {
    if (waiting === job) waiting = null;
  }
  onStatus("Completing payment…");
  return api("capture_intent", payload);
}

export async function mountTerminalEntry(): Promise<CardEntry> {
  const config = await getPaymentConfig();
  const settings = config.serverReader;
  if (!settings?.enabled) throw new Error(settings?.problem || config.terminal?.problem || "The card reader is not set up on this kiosk.");
  const stopWaiting = () => {
    const job = waiting;
    if (!job) return;
    job.stopped = true;
    void api("reader_cancel", job.payload).catch(() => undefined);
  };
  return {
    pay: (payload, onStatus) => collectAndCharge(payload, onStatus),
    cancel: stopWaiting,
    destroy: stopWaiting,
  };
}
