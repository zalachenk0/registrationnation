import { loadStripe, type Stripe, type StripeElements } from "@stripe/stripe-js";

export const PAYMENT_AMOUNT = 11425;
export const PAYMENT_LABEL = "$114.25";
export interface TerminalSettings { enabled: boolean; simulated: boolean; location: string | null; problem: string }
export interface PaymentConfig { publishableKey: string; amount: number; currency: string; testMode: boolean; terminal?: TerminalSettings }
export interface PaymentResult { id: string; status: string; amount: number; testMode: boolean; client_secret?: string; method?: string }
export interface CardEntry {
  pay: (payload: Record<string, string>, onStatus: (text: string) => void) => Promise<PaymentResult>;
  destroy: () => void;
  cancel?: () => void;     // card reader only: stops waiting for a card
  simulated?: boolean;     // card reader only: true when using Stripe's fake reader
}

export async function api(path: string, payload?: Record<string, string>): Promise<any> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(`/api/payment/${path}`, {
      method: path === "config" ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-Kiosk-Payment": "1" },
      body: payload ? JSON.stringify(payload) : undefined, signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Payment service unavailable.");
    return result;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("Payment request timed out. Retry to check this same payment; do not start another customer.");
    throw error;
  } finally { clearTimeout(timer); }
}

export const getPaymentConfig = (): Promise<PaymentConfig> => api("config");

export async function chargeCard(stripe: Stripe, elements: StripeElements, payload: Record<string, string>, onStatus: (text: string) => void): Promise<PaymentResult> {
  onStatus("Checking payment…");
  // Recover the persisted transaction before collecting another card.
  const intent = await api("create_intent", { ...payload, payment_mode: "card" });
  if (intent.status === "succeeded") return intent;
  if (intent.status === "requires_capture") return api("capture_intent", payload);
  if (intent.status === "requires_action") {
    const action = await stripe.handleNextAction({ clientSecret: intent.client_secret });
    if (action.error) throw new Error(action.error.message || "Card authentication was not completed. Retry the same payment.");
    return api("capture_intent", payload);
  }
  if (!["requires_payment_method", "requires_confirmation"].includes(intent.status)) {
    throw new Error(`Payment status: ${intent.status}. Retry to check its status; do not start another charge.`);
  }
  const submitted = await elements.submit();
  if (submitted.error) throw new Error(submitted.error.message || "Complete the card details.");
  onStatus("Confirming your card…");
  const result = await stripe.confirmPayment({
    elements, clientSecret: intent.client_secret,
    confirmParams: { return_url: window.location.origin + "/" }, redirect: "if_required",
  });
  if (result.error) throw new Error((result.error.message || "Card payment was not confirmed.") + " Retry to check the same payment.");
  onStatus("Completing payment…");
  return api("capture_intent", payload);
}

export async function mountCardEntry(host: HTMLElement, onReady: () => void, onError: (text: string) => void): Promise<CardEntry> {
  const config = await getPaymentConfig();
  if (config.amount !== PAYMENT_AMOUNT || config.currency !== "usd" || !config.testMode || !config.publishableKey.startsWith("pk_test_")) {
    throw new Error("Payment configuration does not match this kiosk. Restart with the current project and matching Stripe test keys.");
  }
  const stripe = await loadStripe(config.publishableKey);
  if (!stripe) throw new Error("Stripe could not load. Check this Pi’s internet connection.");
  const elements = stripe.elements({
    mode: "payment", amount: config.amount, currency: config.currency,
    paymentMethodTypes: ["card"], captureMethod: "manual",
    appearance: { theme: "stripe", variables: { colorPrimary: "#1a3a5c", borderRadius: "6px" } },
  });
  const card = elements.create("payment", {
    layout: "tabs", fields: { billingDetails: { name: "auto" } },
    wallets: { applePay: "never", googlePay: "never", link: "never" },
  });
  card.on("ready", onReady);
  card.on("loaderror", event => onError(event.error.message || "Stripe’s card form could not load. Check the connection and retry."));
  card.mount(host);
  return { pay: (payload, onStatus) => chargeCard(stripe, elements, payload, onStatus), destroy: () => card.destroy() };
}
