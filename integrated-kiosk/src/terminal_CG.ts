// ============================================================================
// File:         terminal_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Browser side card reader connection and payment.
// Team-written: All logic written by CG (Cristian Gonzalez), except the lines
//               tagged [3RD PARTY].
// Third-party:  Stripe Terminal JS SDK (@stripe/terminal-js); Stripe Terminal JS
//               SDK (stripe.com/docs/terminal/js).
// ============================================================================
import { loadStripeTerminal } from "@stripe/terminal-js";
import { api, getPaymentConfig, type CardEntry, type PaymentResult, type TerminalSettings } from "./payment_CG";

type StripeTerminalSDK = NonNullable<Awaited<ReturnType<typeof loadStripeTerminal>>>;
type Terminal = ReturnType<StripeTerminalSDK["create"]>;

// one reader connection for the whole kiosk session, so every customer doesn't wait for a reconnect
let terminal: Terminal | null = null;
let connected = false;

// the Terminal SDK asks for this whenever it needs permission to talk to the reader. the secret key stays on the server
async function fetchConnectionToken(): Promise<string> {
  const result = await api("connection_token");
  return result.secret;
}

export async function connectReader(settings: TerminalSettings): Promise<void> {
  if (!terminal) {
    const sdk = await loadStripeTerminal();  // [3RD PARTY: Stripe Terminal JS SDK]
    if (!sdk) throw new Error("Stripe Terminal could not load. Check this kiosk’s internet connection.");
    terminal = sdk.create({
      onFetchConnectionToken: fetchConnectionToken,
      onUnexpectedReaderDisconnect: () => { connected = false; },
    });
  }
  if (connected) return;
  const found = await terminal.discoverReaders(  // [3RD PARTY: Stripe Terminal JS SDK]
    settings.simulated ? { simulated: true } : { simulated: false, location: settings.location ?? undefined },
  );
  if ("error" in found) throw new Error(found.error.message || "Could not search for the card reader.");
  if (found.discoveredReaders.length === 0) {
    throw new Error(settings.simulated
      ? "No simulated card reader was found."
      : "No card reader was found. Check that it is powered on, on the same network as this kiosk, and registered in Stripe.");
  }
  const connection = await terminal.connectReader(found.discoveredReaders[0]);  // [3RD PARTY: Stripe Terminal JS SDK]
  if ("error" in connection) throw new Error(connection.error.message || "Could not connect to the card reader.");
  connected = true;
}

// reader flow: ask our server to open the charge, the reader reads the card, Stripe approves it, our server takes the money
async function collectAndCharge(settings: TerminalSettings, payload: Record<string, string>, onStatus: (text: string) => void): Promise<PaymentResult> {
  if (!connected) {
    onStatus("Reconnecting to the card reader…");
    await connectReader(settings);
  }
  const reader = terminal!;
  onStatus("Checking payment…");
  // the server returns an existing payment for this transaction, so a retry never starts a second charge
  const intent = await api("create_intent", { ...payload, payment_mode: "terminal" });
  if (intent.status === "succeeded") return intent;
  if (intent.status === "requires_capture") return api("capture_intent", payload);
  // the server always hands back a fresh payment when the card was read but never approved, so this is the only unpaid state we can see
  if (intent.status !== "requires_payment_method") {
    throw new Error(`Payment status: ${intent.status}. Retry to check its status; do not start another charge.`);
  }

  // test mode with Stripe's fake reader only: tells it which test card to pretend was used
  if (settings.simulated) reader.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });  // [3RD PARTY: Stripe Terminal JS SDK]

  onStatus("Tap, insert, or swipe the card on the reader…");
  const collected = await reader.collectPaymentMethod(intent.client_secret);  // [3RD PARTY: Stripe Terminal JS SDK]
  if ("error" in collected) {
    const text = collected.error.message || "The card was not read.";
    throw new Error(/cancel/i.test(text) ? "Reader payment canceled. Try the reader again or enter the card manually." : text + " Try again, or enter the card manually.");
  }

  onStatus("Processing your card…");
  const processed = await reader.processPayment(collected.paymentIntent);  // [3RD PARTY: Stripe Terminal JS SDK]
  if ("error" in processed) throw new Error((processed.error.message || "The card was declined.") + " Try another card, or enter the card manually.");
  onStatus("Completing payment…");
  return api("capture_intent", payload);
}

export async function mountTerminalEntry(): Promise<CardEntry> {
  const config = await getPaymentConfig();
  const settings = config.terminal;
  if (!settings?.enabled) throw new Error(settings?.problem || "The card reader is not set up on this kiosk.");
  await connectReader(settings);
  const stopWaiting = () => { void terminal?.cancelCollectPaymentMethod().catch(() => undefined); };  // [3RD PARTY: Stripe Terminal JS SDK]
  return {
    simulated: settings.simulated,
    pay: (payload, onStatus) => collectAndCharge(settings, payload, onStatus),
    cancel: stopWaiting,
    destroy: stopWaiting,
  };
}
