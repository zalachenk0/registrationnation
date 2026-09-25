// sets up the stripe reader connection
// [STRIPE'S SDK] - this exact import path/package name is how Stripe's
// docs say to install and import their Terminal JS SDK for a bundled project
import { loadStripeTerminal } from "@stripe/terminal-js";

// [OUR OWN DESIGN] - Stripe's docs don't define a return-shape convention
// like this; this success/error split was chosen so every call site in
// App.tsx can check .success instead of using try/catch everywhere
// What chargeCard() returns
// either it worked, or here's why it didn't
type ChargeResult =
  | { success: true; paymentIntentId: string }
  | { success: false; error: string };

// [OUR OWN DESIGN] - not a Stripe pattern at all, just a TypeScript trick
// to pull the SDK's own type out instead of writing it by hand
// Grabbing the reader's type straight from the SDK
type StripeTerminalSDK = NonNullable<Awaited<ReturnType<typeof loadStripeTerminal>>>;
type Terminal = ReturnType<StripeTerminalSDK["create"]>;

// [OUR OWN DESIGN] - reusing one connection instead of reconnecting every
// payment isn't something Stripe's docs tell you to do, just seemed sensible
// keeps the reader connection so we don't have to reconnect every transaction
let terminal: Terminal | null = null;
let readerIsConnected = false;

// [STRIPE'S SDK, our endpoint] - the SDK requires an onFetchConnectionToken
// callback with exactly this signature (returns a Promise<string>); the
// /api/payment/connection_token URL itself is ours, not Stripe's
// stripe sdk calls this when it needs a token to connect to the reader
async function fetchConnectionToken(): Promise<string> {
  const response = await fetch("/api/payment/connection_token", { method: "POST" });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Could not get a connection token.");
  }
  return data.secret;
}

// connects to the reader. 
//runs automatically first time, then reuses the connection
export async function connectToReader(): Promise<void> {
  if (terminal) {
    return;
  }

  const StripeTerminal = await loadStripeTerminal();
  if (!StripeTerminal) {
    throw new Error("Stripe Terminal SDK could not be loaded.");
  }

  // [STRIPE'S SDK] - StripeTerminal.create() with these two callback names
  // (onFetchConnectionToken, onUnexpectedReaderDisconnect) is Stripe's
  // documented setup call, not something we invented
  terminal = StripeTerminal.create({
    onFetchConnectionToken: fetchConnectionToken,
    onUnexpectedReaderDisconnect: () => {
      readerIsConnected = false;
    },
  });

  // [STRIPE'S SDK] - discoverReaders({ simulated: true }) is Stripe's own
  // documented reader simulator, specifically confirmed by checking their
  // testing docs mid-project (docs.stripe.com/terminal/references/testing)
  // using the simulated reader for now so we can test without hardware
  //
  // swap this line when we get real terminal
  //   const discoverResult = await terminal.discoverReaders({ location: "<your Stripe location id>" });
  const discoverResult = await terminal.discoverReaders({ simulated: true });

  // [OUR OWN DESIGN] - the error-checking/throwing pattern here is ours;
  // Stripe's docs show the call, not how to handle every failure case
  if ("error" in discoverResult || discoverResult.discoveredReaders.length === 0) {
    throw new Error("No card reader found.");
  }

  const connectResult = await terminal.connectReader(discoverResult.discoveredReaders[0]);
  if ("error" in connectResult) {
    throw new Error("Could not connect to the card reader.");
  }

  readerIsConnected = true;
}

//main function everything calls and we give it cents (7100 = $71.00)
// returns whether it worked or not
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

    // [OUR OWN DESIGN, our endpoint] - /api/payment/create_intent is our
    // route, not Stripe's; the underlying idea (server creates the charge,
    // client gets a client_secret back) is Stripe's documented pattern
    // Step 1: start the charge on our server
    const createResponse = await fetch("/api/payment/create_intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: amountCents, receiptEmail }),
    });
    const intent = await createResponse.json();
    if (!createResponse.ok) {
      throw new Error(intent.error || "Could not start the payment.");
    }

    // [STRIPE'S SDK] - setSimulatorConfiguration + this exact test card
    // number (4242 4242 4242 4242) are both Stripe's own documented test
    // tooling, confirmed via their testing docs
    // Step 2: used in test mode only and tells the reader and gives it a test number
    if (terminal.setSimulatorConfiguration) {
      terminal.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });
    }

    // [STRIPE'S SDK] - collectPaymentMethod() and processPayment() (below)
    // are Stripe's core Terminal SDK methods, called in the order their
    // docs specify
    // Step 3: reader reads the card
    const collected = await terminal.collectPaymentMethod(intent.client_secret);
    if ("error" in collected) {
      throw new Error(collected.error.message);
    }

    // Step 4: stripe checks the card is good to charge
    const processed = await terminal.processPayment(collected.paymentIntent);
    if ("error" in processed) {
      throw new Error(processed.error.message);
    }

    // [OUR OWN DESIGN, our endpoint] - /api/payment/capture_intent is ours;
    // "capture after the reader confirms" is Stripe's documented manual
    // capture pattern, but this specific route/request shape is ours
    // Step 5: if approved it takes the money for real
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
    // [OUR OWN DESIGN] - catching everything and converting to
    // { success: false } instead of letting it throw is our choice
    console.error("chargeCard failed:", error);
    const message = error instanceof Error ? error.message : "Payment failed.";
    return { success: false, error: message };
  }
}
