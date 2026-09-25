// sets up the stripe reader connection
import { loadStripeTerminal } from "@stripe/terminal-js";

// What chargeCard() returns
// either it worked, or here's why it didn't
type ChargeResult =
  | { success: true; paymentIntentId: string }
  | { success: false; error: string };

// Grabbing the reader's type straight from the SDK
type StripeTerminalSDK = NonNullable<Awaited<ReturnType<typeof loadStripeTerminal>>>;
type Terminal = ReturnType<StripeTerminalSDK["create"]>;

// keeps the reader connection so we don't have to reconnect every transaction
let terminal: Terminal | null = null;
let readerIsConnected = false;

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

  terminal = StripeTerminal.create({
    onFetchConnectionToken: fetchConnectionToken,
    onUnexpectedReaderDisconnect: () => {
      readerIsConnected = false;
    },
  });

  // using the simulated reader for now so we can test without hardware
  //
  // swap this line when we get real terminal
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

    // Step 2: used in test mode only and tells the reader and gives it a test number
    if (terminal.setSimulatorConfiguration) {
      terminal.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });
    }

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
    console.error("chargeCard failed:", error);
    const message = error instanceof Error ? error.message : "Payment failed.";
    return { success: false, error: message };
  }
}
