// payment.ts, browser side
//
// takes payment by talking to our stripe server backend (scanner_api.py)
// calls chargeCard(amountCents) and checks the result
// also pulls email from form have stripe email a official receipt chargeCard(7100, customerEmail)
//
// the browser side and server side are kept separate so that the secret key only lives in the server and not in the browser where it can be stolen from 
// 

import { loadStripeTerminal } from "@stripe/terminal-js";

type ChargeResult =
  | { success: true; paymentIntentId: string }
  | { success: false; error: string };

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

// connects to the card reader. chargeCard() automatically calls this
// the first time it's needed and the same connection is used after that
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

  // { simulated: true } is used to use stripe's simulated reader,
  //
  // when the real terminal arrives, we'll replace it with:
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

// this is the main function the app calls
// we give it a dollar amount in cents like 7100 for $71.00, not 71. 
// Walks through the reader flow and returns whether it worked.
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

    // Step 2: test mode only, tells the simulated reader which test card to
    // pretend was used. this block is to be deleted when the real terminal
    // is implemented
    if (terminal.setSimulatorConfiguration) {
      terminal.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });
    }

    // Step 3: the reader reads the card
    const collected = await terminal.collectPaymentMethod(intent.client_secret);
    if ("error" in collected) {
      throw new Error(collected.error.message);
    }

    // Step 4: stripe confirms the card is good to charge
    const processed = await terminal.processPayment(collected.paymentIntent);
    if ("error" in processed) {
      throw new Error(processed.error.message);
    }

    // Step 5: card was read successfully and takes the money
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
