// ============================================================================
// File:         payment_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Browser side payment helpers: calls to our payment server and
//               the shared payment types. Card reader only (no typed card).
// Team-written: All logic written by CG (Cristian Gonzalez), except the lines
//               tagged [BV] (Blas Vargas, Nov 17).
// Third-party:  None (the Stripe calls happen on the server).
// ============================================================================
import { kioskFetch } from "./integration_BV";  // [BV] adds the kiosk header and the signed-out check to every call
export const PAYMENT_AMOUNT = 11425;
export const PAYMENT_LABEL = "$114.25";
export interface TerminalSettings { enabled: boolean; simulated: boolean; location: string | null; problem: string }
export interface ServerReaderSettings { enabled: boolean; simulated: boolean; problem: string }
export interface PaymentConfig { amount: number; currency: string; testMode: boolean; terminal?: TerminalSettings; serverReader?: ServerReaderSettings }
export interface PaymentResult { id: string; status: string; amount: number; testMode: boolean; client_secret?: string; method?: string; receiptEmail?: string | null; reader?: string; readerMessage?: string }
export interface CardEntry {
  pay: (payload: Record<string, string>, onStatus: (text: string) => void) => Promise<PaymentResult>;
  destroy: () => void;
  cancel?: () => void;     // stops waiting for a card
}

export async function api(path: string, payload?: Record<string, string>): Promise<any> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await kioskFetch(`/api/payment/${path}`, {  // [BV] was fetch
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
