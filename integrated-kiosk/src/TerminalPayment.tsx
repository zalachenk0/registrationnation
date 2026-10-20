import { useEffect, useState, type MutableRefObject } from "react";
import { PAYMENT_LABEL, type CardEntry } from "./payment";
import { mountTerminalEntry } from "./terminal";

// the S700 card reader panel. This is the primary way to pay; onUseManual switches to the typed-card backup
export function TerminalPayment({ controllerRef, onReady, status, paid, busy, onUseManual }: {
  controllerRef: MutableRefObject<CardEntry | null>;
  onReady: (ready: boolean) => void;
  status: string; paid: boolean; busy: boolean;
  onUseManual: () => void;
}) {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [simulated, setSimulated] = useState(false);
  useEffect(() => {
    if (paid) return;
    let active = true;
    let entry: CardEntry | null = null;
    setError(""); setLoading(true); onReady(false);
    const fail = (text: string) => { if (active) { setError(text); setLoading(false); onReady(false); } };
    const timer = setTimeout(() => fail("The card reader is taking too long to respond. Check that it is on and on the same network as this kiosk, then retry, or enter the card manually."), 30000);
    mountTerminalEntry().then(value => {
      entry = value;
      if (!active) { value.destroy(); return; }
      clearTimeout(timer);
      controllerRef.current = value;
      setSimulated(!!value.simulated); setLoading(false); setError(""); onReady(true);
    }).catch(reason => { clearTimeout(timer); fail(reason instanceof Error ? reason.message : "Could not connect to the card reader."); });
    return () => { active = false; clearTimeout(timer); entry?.destroy(); controllerRef.current = null; onReady(false); };
  }, [attempt, controllerRef, onReady, paid]);

  const connected = !loading && !error;
  return <div className="flex flex-col gap-6">
    <div className="rounded-lg p-4" style={{ backgroundColor: "#fffbea", border: "1px solid #f5d76e" }}>
      <p className="text-sm font-semibold" style={{ color: "#b45309" }}>Stripe Terminal — Test Mode</p>
      <p className="text-sm mt-1">{simulated
        ? "Simulated card reader: no physical card is needed. Select Pay to run a test charge."
        : "Select Pay, then tap, insert, or swipe a Stripe test card on the card reader."}</p>
      {!simulated && <p className="text-xs mt-2">Test mode only works with Stripe test cards, for example 4242 4242 4242 4242.</p>}
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
    {!paid && <button type="button" disabled={busy} onClick={onUseManual} className="px-4 py-2 rounded self-start text-sm" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>Reader not working? Enter the card manually instead</button>}
    <p className="text-xs text-center" style={{ color: "var(--muted-foreground)" }}>Card details are handled by the Stripe card reader, not by this kiosk.</p>
  </div>;
}
