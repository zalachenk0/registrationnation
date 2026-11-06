// ============================================================================
// File:         TerminalPayment_CG.tsx
// Written by:   CG (Cristian Gonzalez)
// What it does: Card reader payment panel for the Stripe reader.
// Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
//               only through the third-party sources listed below.
// Third-party:  React; Stripe (server-driven reader), through terminal_CG.ts.
// ============================================================================
import { useEffect, useState, type MutableRefObject } from "react";
import { PAYMENT_LABEL, type CardEntry } from "./payment_CG";
import { mountTerminalEntry } from "./terminal_CG";

// the S700 card reader panel. [CG] This is the only way to pay: there's no typed-card backup, because the kiosk has no
// keyboard. A reader problem shows here with Retry connecting
export function TerminalPayment({ controllerRef, onReady, status, paid, busy }: {
  controllerRef: MutableRefObject<CardEntry | null>;
  onReady: (ready: boolean) => void;
  status: string; paid: boolean; busy: boolean;
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
  return <div className="flex flex-col gap-6">
    <div className="rounded-lg p-4" style={{ backgroundColor: "#fffbea", border: "1px solid #f5d76e" }}>
      <p className="text-sm font-semibold" style={{ color: "#b45309" }}>Stripe Terminal — Test Mode</p>
      <p className="text-sm mt-1">Select Pay, then tap, insert, or swipe a Stripe test card on the card reader.</p>
      <p className="text-xs mt-2">Test mode only works with Stripe test cards, for example 4242 4242 4242 4242.</p>
    </div>
    <div className="rounded-lg p-5 flex justify-between items-center" style={{ border: "1px solid var(--border)", backgroundColor: "var(--secondary)" }}>
      <span className="font-semibold">Registration renewal total</span>
      <span className="text-xl font-bold">{PAYMENT_LABEL}</span>
    </div>
    {paid ? <p role="status">Payment confirmed. You can retry printing without paying again.</p> : <>
      <p role="status" className="flex items-center gap-2 text-sm">
        <span aria-hidden style={{ width: 10, height: 10, borderRadius: "50%", display: "inline-block", backgroundColor: connected ? "#3a7d44" : error ? "#b42318" : "#d4a017" }} />
        {loading ? "Connecting to the card reader…" : error ? "Card reader not connected" : "Card reader connected and ready"}
      </p>
      {error && <div role="alert" className="text-sm">
        <p>{error}</p>
        <button type="button" disabled={busy} onClick={() => setAttempt(value => value + 1)} className="mt-3 px-4 py-2 rounded" style={{ backgroundColor: "var(--primary)", color: "#fff" }}>Retry connecting</button>
      </div>}
    </>}
    {status && <p role="status" aria-live="polite" className="text-sm font-semibold">{status}</p>}
    {busy && !paid && <button type="button" onClick={() => controllerRef.current?.cancel?.()} className="px-4 py-2 rounded self-start text-sm" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>Cancel card reader payment</button>}
    <p className="text-xs text-center" style={{ color: "var(--muted-foreground)" }}>Card details are handled by the Stripe card reader, not by this kiosk.</p>
  </div>;
}
