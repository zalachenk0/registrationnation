// ============================================================================
// File:         TerminalPayment_CG.tsx
// Written by:   CG (Cristian Gonzalez)
// What it does: Card reader payment panel for the Stripe reader.
// Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
//               only through the third-party sources listed below. BV shortened
//               the reader instructions on Nov 17; the new status panel
//               replaced that text.
// Third-party:  React; Stripe (server-driven reader), through terminal_CG.ts.
// ============================================================================
import { useEffect, useState, type MutableRefObject } from "react";
import { PAYMENT_LABEL, CHARGES, dollars, type CardEntry } from "./payment_CG";
import { mountTerminalEntry } from "./terminal_CG";

// the S700 card reader panel. [CG] This is the only way to pay: there's no typed-card backup, because the kiosk has no
// keyboard. A reader problem shows here with Retry connecting
export function TerminalPayment({ controllerRef, onReady, status, paid, busy, error: payError = "" }: {
  controllerRef: MutableRefObject<CardEntry | null>;
  onReady: (ready: boolean) => void;
  status: string; paid: boolean; busy: boolean;
  error?: string;  // why the last attempt stopped (declined, canceled, timed out)
}) {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (paid) return;
    let active = true;
    let entry: CardEntry | null = null;
    setError(""); setLoading(true); onReady(false);
    const fail = (text: string) => { if (active) { setError(text); setLoading(false); onReady(false); } };
    const timer = setTimeout(() => fail("The card reader is taking too long to respond. Check that it is on and connected to the internet, then retry."), 30000);
    mountTerminalEntry().then(value => {
      entry = value;
      if (!active) { value.destroy(); return; }
      clearTimeout(timer);
      controllerRef.current = value;
      setLoading(false); setError(""); onReady(true);
    }).catch(reason => { clearTimeout(timer); fail(reason instanceof Error ? reason.message : "Could not connect to the card reader."); });
    return () => { active = false; clearTimeout(timer); entry?.destroy(); controllerRef.current = null; onReady(false); };
  }, [attempt, controllerRef, onReady, paid]);

  const connected = !loading && !error;
  // sponsor wanted the reader instructions to stand out more. one big panel that says what to do right now:
  // wait, tap your card, or what went wrong. payment starts on its own once the reader connects (App_BV_CG.tsx)
  const waitingForCard = busy && !paid && /tap|insert|swipe/i.test(status);
  // paid but the print request failed: say so. this used to still read "printing" and hid the error
  const printFailed = paid && !busy && Boolean(payError);
  const headline = printFailed ? "Your payment went through, but the receipt didn't print"
    : paid ? "Payment approved"
    : payError ? "Payment didn't go through"
    : error ? "The card reader isn't connected"
    : loading ? "Getting the card reader ready…"
    : waitingForCard ? "Tap, insert, or swipe your card on the card reader"
    : busy ? status || "Starting your payment…"
    : "Ready to pay";
  const detail = printFailed ? payError + " Tap Retry Printing below, or get a staff member."
    : paid ? "You won't be charged again. Your sticker and receipt are printing."
    : payError ? payError
    : error ? error
    : loading ? "This takes a few seconds."
    : waitingForCard ? "The reader is showing $114.25. Follow its screen until it says approved."
    : busy ? "Please wait. Don't remove your card."
    : "The payment will start on the card reader in a moment.";
  const tone = printFailed ? { bg: "#fdecea", border: "#b42318", text: "#7a1a12" }
    : paid ? { bg: "#e6f4e8", border: "#3a7d44", text: "#1f5129" }
    : payError || error ? { bg: "#fdecea", border: "#b42318", text: "#7a1a12" }
    : { bg: "#fff4ec", border: "var(--accent)", text: "#7c2d12" };
  return <div className="flex flex-col gap-6">
    <style>{`@keyframes reader-pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(242,101,34,0.45) } 50% { box-shadow: 0 0 0 14px rgba(242,101,34,0) } }`}</style>
    <div role="status" aria-live="polite" className="rounded-xl p-6 flex items-center gap-5"
      style={{ backgroundColor: tone.bg, border: `3px solid ${tone.border}`, animation: waitingForCard ? "reader-pulse 1.6s ease-in-out infinite" : "none" }}>
      <svg width="64" height="64" viewBox="0 0 64 64" fill="none" aria-hidden="true" className="shrink-0" style={{ color: tone.border }}>
        <rect x="14" y="4" width="36" height="56" rx="6" stroke="currentColor" strokeWidth="3" />
        <rect x="20" y="10" width="24" height="16" rx="2" fill="currentColor" opacity="0.25" />
        <path d="M24 36h16M24 42h16M24 48h10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <div className="min-w-0">
        <p className="text-2xl font-bold leading-tight" style={{ color: tone.text }}>{headline}</p>
        <p className="text-base mt-2" style={{ color: tone.text }}>{detail}</p>
      </div>
    </div>
    {/* summary of charges, same rows as the printed receipt, adding up to the total on the reader */}
    <div data-charges className="rounded-lg p-5 flex flex-col gap-2" style={{ border: "1px solid var(--border)", backgroundColor: "var(--secondary)" }}>
      <p className="font-semibold mb-1">Summary of charges</p>
      {CHARGES.map(charge => (
        <div key={charge.label} className="flex justify-between items-center text-base">
          <span>{charge.label}</span>
          <span className="tabular-nums">{dollars(charge.cents)}</span>
        </div>
      ))}
      <div className="flex justify-between items-center pt-2 mt-1" style={{ borderTop: "1px solid var(--border)" }}>
        <span className="font-semibold">Total due</span>
        <span className="text-xl font-bold tabular-nums">{PAYMENT_LABEL}</span>
      </div>
    </div>
    {!paid && <p className="flex items-center gap-2 text-sm">
      <span aria-hidden style={{ width: 10, height: 10, borderRadius: "50%", display: "inline-block", backgroundColor: connected ? "#3a7d44" : error ? "#b42318" : "#d4a017" }} />
      {loading ? "Connecting to the card reader…" : error ? "Card reader not connected" : "Card reader connected"}
      <span className="ml-2 text-xs" style={{ color: "var(--muted-foreground)" }}>Test mode</span>
    </p>}
    {error && !paid && <button type="button" disabled={busy} onClick={() => setAttempt(value => value + 1)} className="px-5 py-3 rounded self-start" style={{ backgroundColor: "var(--primary)", color: "#fff" }}>Retry connecting</button>}
    {busy && !paid && <button type="button" onClick={() => controllerRef.current?.cancel?.()} className="px-5 py-3 rounded self-start text-base" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>Cancel payment</button>}
  </div>;
}
