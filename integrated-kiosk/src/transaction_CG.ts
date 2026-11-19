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
//  RN1      Registration Nation kiosk 1 (the location and kiosk number)
//  20261119 the date, UTC
//  075512   the time, UTC (07:55:12)
//  A3F9C2   6 random hex digits, so two renewals in the same second never share an ID
// 26 characters: fits the receipt's Transaction ID box on one line, and is well inside the
// 8-64 characters printing_BV.py and payment_service_CG.py accept. receipt_documents_BV.py reads
// the time back out of it, so the format there has to change with this one.
export const KIOSK_NUMBER = 1;

export function transactionId(now: Date = new Date(), kiosk: number = KIOSK_NUMBER): string {
  const iso = now.toISOString();  // always UTC, whatever time zone the Pi is set to
  const date = iso.slice(0, 10).replace(/-/g, "");
  const time = iso.slice(11, 19).replace(/:/g, "");
  const random = Array.from(crypto.getRandomValues(new Uint8Array(3)), byte => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
  return `RN${kiosk}-${date}-${time}-${random}`;
}
