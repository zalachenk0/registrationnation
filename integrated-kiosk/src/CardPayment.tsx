import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { mountCardEntry, PAYMENT_LABEL, type CardEntry } from "./payment";

export function CardPayment({ controllerRef, onReady, status, paid, busy, onUseReader, notice }: {
  controllerRef: MutableRefObject<CardEntry | null>;
  onReady: (ready: boolean) => void;
  status: string; paid: boolean; busy: boolean;
  onUseReader?: () => void;  // when set, offers a way back to the card reader
  notice?: string;           // e.g. why the card reader isn't available
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (paid || !host.current) return;
    let active = true;
    let entry: CardEntry | null = null;
    setError(""); setLoading(true); onReady(false);
    const fail = (text: string) => { if (active) { setError(text); setLoading(false); onReady(false); } };
    const timer = setTimeout(() => fail("The card form is taking too long to load. Check the internet connection, then retry."), 30000);
    mountCardEntry(host.current, () => {
      if (active) { clearTimeout(timer); setLoading(false); setError(""); onReady(true); }
    }, fail).then(value => {
      entry = value;
      if (!active) value.destroy();
      else controllerRef.current = value;
    }).catch(reason => fail(reason instanceof Error ? reason.message : "Stripe could not load."));
    return () => { active = false; clearTimeout(timer); entry?.destroy(); controllerRef.current = null; onReady(false); };
  }, [attempt, controllerRef, onReady, paid]);

  return <div className="flex flex-col gap-6">
    <div className="rounded-lg p-4" style={{ backgroundColor: "#fffbea", border: "1px solid #f5d76e" }}>
      <p className="text-sm font-semibold" style={{ color: "#b45309" }}>Stripe — Test Mode</p>
      <p className="text-sm mt-1">Enter a test card below. No real money is charged.</p>
      <p className="text-xs mt-2">For a successful test: 4242 4242 4242 4242, a future expiry date, and any three-digit CVC.</p>
    </div>
    <div className="rounded-lg p-5 flex justify-between items-center" style={{ border: "1px solid var(--border)", backgroundColor: "var(--secondary)" }}>
      <span className="font-semibold">Registration renewal total</span>
      <span className="text-xl font-bold">{PAYMENT_LABEL}</span>
    </div>
    {paid ? <p role="status">Payment confirmed. You can retry printing without paying again.</p> : <>
      {loading && <p role="status" className="text-sm">Loading secure card fields…</p>}
      <div ref={host} aria-label="Secure card details" style={{ pointerEvents: busy ? "none" : "auto" }} />
      {error && <div role="alert" className="text-sm">
        <p>{error}</p>
        <button type="button" disabled={busy} onClick={() => setAttempt(value => value + 1)} className="mt-3 px-4 py-2 rounded" style={{ backgroundColor: "var(--primary)", color: "#fff" }}>Reload card form</button>
      </div>}
    </>}
    {status && <p role="status" aria-live="polite" className="text-sm font-semibold">{status}</p>}
    {notice && !paid && <p className="text-xs" role="status" style={{ color: "#b45309" }}>Card reader not available: {notice}</p>}
    {onUseReader && !paid && <button type="button" disabled={busy} onClick={onUseReader} className="px-4 py-2 rounded self-start text-sm" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>Use the card reader instead</button>}
    <p className="text-xs text-center" style={{ color: "var(--muted-foreground)" }}>Card details are entered securely through Stripe.</p>
  </div>;
}
