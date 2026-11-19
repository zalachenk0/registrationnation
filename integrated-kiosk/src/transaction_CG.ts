// ============================================================================
// File:         transaction_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Makes each renewal's transaction ID from the time, the kiosk
//               and a random part, short enough to fit on one line of the
//               printed receipt.
// Team-written: All logic written by CG (Cristian Gonzalez). The time-based
//               format follows BV's earlier ID.
// Third-party:  None (the browser's crypto.getRandomValues).
// ============================================================================

// RN1-20261119-075512-A3F9C2
//  RN1      Registration Nation, kiosk 1
//  20261119 date (UTC)
//  075512   time (UTC)
//  A3F9C2   6 random hex digits so two renewals in the same second don't collide
// 26 chars total, fits the receipt box on one line and stays inside the 8-64 the print and payment code allow.
// receipt_documents_BV.py parses the time back out, so if this format changes, change it there too
export const KIOSK_NUMBER = 1;

export function transactionId(now: Date = new Date(), kiosk: number = KIOSK_NUMBER): string {
  const iso = now.toISOString();  // UTC no matter what time zone the Pi is on
  const date = iso.slice(0, 10).replace(/-/g, "");
  const time = iso.slice(11, 19).replace(/:/g, "");
  const random = Array.from(crypto.getRandomValues(new Uint8Array(3)), byte => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
  return `RN${kiosk}-${date}-${time}-${random}`;
}
