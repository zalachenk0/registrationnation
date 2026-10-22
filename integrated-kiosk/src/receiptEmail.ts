// receiptEmail.ts, browser side
//
// asks our flask server to email the customer their texas tag & title receipt (a pdf, no signature block)
// the mail password is a secret so it only lives on the server (.env), this file never sees it
// it sends the same kiosk header as the card payment calls (see payment.ts)

// our design
// either it went out (or had already gone out for this payment), or it didn't
// notConfigured means nobody has set up receipt email on this kiosk yet. that's a back-office matter,
// so the caller shows the customer nothing at all in that case (the server writes it to the office log)
export type EmailResult =
  | { success: true; to: string; already: boolean }
  | { success: false; error: string; notConfigured: boolean };

export async function emailReceipt(payload: Record<string, string>): Promise<EmailResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch("/api/payment/email_receipt", {   // our route, not a mail server
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Kiosk-Payment": "1" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));   // in case the server sent back something that isn't json
    if (!response.ok) {
      return { success: false, error: data.error || `Receipt email failed (${response.status}).`, notConfigured: Boolean(data.notConfigured) };
    }
    return { success: true, to: data.to, already: Boolean(data.already) };
  } catch (error) {
    // our design - a mail problem should never look like the payment failed, so the caller just shows it on its own line
    return { success: false, error: error instanceof Error ? error.message : "The receipt email could not be sent.", notConfigured: false };
  } finally {
    clearTimeout(timer);
  }
}
