// payment.ts
//
// Handles taking a card payment on the Stripe Terminal card reader.
// This is the payment system's entire surface area for the rest of
// the app: call chargeCard(amountCents) and check the result. Pass a
// second argument to also have Stripe email an official receipt:
//   chargeCard(7100, customerEmail)
//
// This talks to our own Flask backend (scanner_api.py) at /api/payment/*,
// never to Stripe directly - the secret key lives only on the server.

import { loadStripeTerminal } from "@stripe/terminal-js";

type ChargeResult =
  | { success: true; paymentIntentId: string }
  | { success: false; error: string };

// The object @stripe/terminal-js's loadStripeTerminal() resolves to,
// with null (its "not in a browser" case) removed - we only ever call
// this from the browser.
type StripeTerminalSDK = NonNullable<Awaited<ReturnType<typeof loadStripeTerminal>>>;
type Terminal = ReturnType<StripeTerminalSDK["create"]>;

let terminal: Terminal | null = null;
let readerIsConnected = false;

async function fetchConnectionToken(): Promise<string> {
  const response = await fetch("/api/payment/connection_token", { method: "POST" });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Could not get a connection token.");
  }
  return data.secret;
}

// Connects to the card reader. chargeCard() calls this automatically
// the first time it's needed, and the same connection is reused after
// that - you don't normally need to call this yourself, though it's
// exported so the UI can show "connecting..." before the customer taps
// the pay button.
export async function connectToReader(): Promise<void> {
  if (terminal) {
    return;
  }

  const StripeTerminal = await loadStripeTerminal();
  if (!StripeTerminal) {
    throw new Error("Stripe Terminal SDK could not be loaded.");
  }

  terminal = StripeTerminal.create({
    onFetchConnectionToken: fetchConnectionToken,
    onUnexpectedReaderDisconnect: () => {
      readerIsConnected = false;
    },
  });

  // { simulated: true } uses Stripe's practice reader, which is why
  // this works with no physical hardware plugged in.
  //
  // >>> WHEN THE PHYSICAL S700 ARRIVES, CHANGE ONLY THIS ONE LINE <<<
  // Replace it with:
  //   const discoverResult = await terminal.discoverReaders({ location: "<your Stripe location id>" });
  const discoverResult = await terminal.discoverReaders({ simulated: true });

  if ("error" in discoverResult || discoverResult.discoveredReaders.length === 0) {
    throw new Error("No card reader found.");
  }

  const connectResult = await terminal.connectReader(discoverResult.discoveredReaders[0]);
  if ("error" in connectResult) {
    throw new Error("Could not connect to the card reader.");
  }

  readerIsConnected = true;
}

// The main function the app calls. Give it a dollar amount in CENTS
// (7100 for $71.00, not 71). Walks through the reader flow and returns
// whether it worked. receiptEmail is optional - if given, Stripe
// automatically emails an official payment receipt once the charge
// succeeds (separate from the printed sticker/receipt mockup).
export async function chargeCard(
  amountCents: number,
  receiptEmail?: string
): Promise<ChargeResult> {
  try {
    if (!readerIsConnected) {
      await connectToReader();
    }
    if (!terminal) {
      throw new Error("Card reader is not connected.");
    }

    // Step 1: open a charge on our server. No money has moved yet.
    const createResponse = await fetch("/api/payment/create_intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: amountCents, receiptEmail }),
    });
    const intent = await createResponse.json();
    if (!createResponse.ok) {
      throw new Error(intent.error || "Could not start the payment.");
    }

    // Step 2: test mode only. Tells the fake reader which test card to
    // pretend was used. Delete this block once using the real S700 -
    // a real card supplies its own data when it's tapped.
    if (terminal.setSimulatorConfiguration) {
      terminal.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });
    }

    // Step 3: the reader reads the card.
    const collected = await terminal.collectPaymentMethod(intent.client_secret);
    if ("error" in collected) {
      throw new Error(collected.error.message);
    }

    // Step 4: Stripe confirms the card is good to charge.
    const processed = await terminal.processPayment(collected.paymentIntent);
    if ("error" in processed) {
      throw new Error(processed.error.message);
    }

    // Step 5: now that the card was read successfully, take the money.
    const captureResponse = await fetch("/api/payment/capture_intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paymentIntentId: intent.id }),
    });
    const captureResult = await captureResponse.json();

    if (!captureResponse.ok || captureResult.status !== "succeeded") {
      throw new Error(captureResult.error || "Payment could not be completed.");
    }

    return { success: true, paymentIntentId: captureResult.id };
  } catch (error) {
    console.error("chargeCard failed:", error);
    const message = error instanceof Error ? error.message : "Payment failed.";
    return { success: false, error: message };
  }
}
