// stripe sdk
import { loadStripeTerminal } from "@stripe/terminal-js"; // sets up the stripe reader connection

// our design
type ChargeResult =                            // either it worked, or here's why it didn't
  | { success: true; paymentIntentId: string }
  | { success: false; error: string };

// our design
type StripeTerminalSDK = NonNullable<Awaited<ReturnType<typeof loadStripeTerminal>>>;  // take the reader's type straight from the SDK for accuracy
type Terminal = ReturnType<StripeTerminalSDK["create"]>;

let terminal: Terminal | null = null; // reuses terminal connection rather than connecting with each transaction
let readerIsConnected = false;

// stripe sdk
async function fetchConnectionToken(): Promise<string> {
  const response = await fetch("/api/payment/connection_token", { method: "POST" });  // stripe sdk calls this when it needs a token to connect to the reader
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Could not get a connection token.");
  }
  return data.secret;
}

export async function connectToReader(): Promise<void> {   // connects to the reader and runs automatically first time then reuses the connection for each following transaction
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

  const discoverResult = await terminal.discoverReaders({ simulated: true });   // swap this line when we get real terminal
                                                                                // const discoverResult = await terminal.discoverReaders({ location: "<your Stripe location id>" });

  // our design
  if ("error" in discoverResult || discoverResult.discoveredReaders.length === 0) { // spits out an error message
    throw new Error("No card reader found.");
  }

  const connectResult = await terminal.connectReader(discoverResult.discoveredReaders[0]);
  if ("error" in connectResult) {
    throw new Error("Could not connect to the card reader.");
  }

  readerIsConnected = true;
}

// stripe sdk

export async function chargeCard(     // main function everything calls and we give it cents (7100 = $71.00) and returns whether it worked or not
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
   
    const createResponse = await fetch("/api/payment/create_intent", {   // Step 1: start the charge on our server
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: amountCents, receiptEmail }),
    });
    const intent = await createResponse.json();
    if (!createResponse.ok) {
      throw new Error(intent.error || "Could not start the payment.");
    }
  
    if (terminal.setSimulatorConfiguration) {
      terminal.setSimulatorConfiguration({ testCardNumber: "4242424242424242" });    // Step 2: used in test mode only and tells the reader and gives it a test number
    }
    const collected = await terminal.collectPaymentMethod(intent.client_secret);    // Step 3: reader reads the card
    if ("error" in collected) {
      throw new Error(collected.error.message);
    }
    const processed = await terminal.processPayment(collected.paymentIntent);      // Step 4: stripe checks the card is good to charge
    if ("error" in processed) {
      throw new Error(processed.error.message);
    }
    const captureResponse = await fetch("/api/payment/capture_intent", {          // Step 5: if approved it takes the money for real
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
    
    // our design 
    console.error("chargeCard failed:", error);  // spits error message
    const message = error instanceof Error ? error.message : "Payment failed.";
    return { success: false, error: message };
  }
}
