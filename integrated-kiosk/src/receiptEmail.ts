// receiptEmail.ts, browser side
//
// asks our flask server to email the customer their texas tag & title receipt (a pdf, no signature block)
// the mail password is a secret so it only lives on the server (.env), this file never sees it
// it goes through the same payment api helper as the card payment, so it carries the same kiosk header

import { api } from "./payment";

// our design
// either it went out (or had already gone out for this payment), or here's why it didn't
export type EmailResult =
  | { success: true; to: string; already: boolean }
  | { success: false; error: string };

export async function emailReceipt(payload: Record<string, string>): Promise<EmailResult> {
  try {
    const result = await api("email_receipt", payload);   // our route: /api/payment/email_receipt
    return { success: true, to: result.to, already: Boolean(result.already) };
  } catch (error) {
    // our design - a mail problem should never look like the payment failed, so the caller just shows it on its own line
    return { success: false, error: error instanceof Error ? error.message : "The receipt email could not be sent." };
  }
}
