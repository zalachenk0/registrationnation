// ============================================================================
// File:         StaffPanel_CG.tsx
// Written by:   CG (Cristian Gonzalez)
// What it does: Hidden staff panel (who's signed in, Sign out). Replaces the
//               sign-out bar customers could see and tap. Only opens from a
//               plugged-in keyboard with Ctrl+Shift+L; Esc or Close hides it.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  React.
// ============================================================================
import { useEffect, useState } from "react";
import type { StaffUser } from "./integration_BV";

// Ctrl+Shift+L. the on-screen keyboard can't hold Ctrl, so a customer can't open this from the touchscreen.
// checks the physical key (code) so caps lock or a different layout doesn't matter
export function isStaffShortcut(event: { ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean; code: string; repeat?: boolean }) {
  return event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey && event.code === "KeyL" && !event.repeat;
}

export function StaffPanel({ staff, onSignOut }: { staff: StaffUser | null; onSignOut: () => Promise<string> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isStaffShortcut(event)) {
        event.preventDefault();
        setMessage("");
        setOpen(current => !current);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // closes itself after a minute so it's never left open for the next customer
  useEffect(() => {
    if (!open || busy) return;
    const timer = setTimeout(() => setOpen(false), 60000);
    return () => clearTimeout(timer);
  }, [open, busy, message]);

  if (!open) return null;
  const signOut = async () => {
    setBusy(true); setMessage("");
    try {
      const problem = await onSignOut();
      if (problem) setMessage(problem); else setOpen(false);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="dialog" aria-label="Staff" data-staff-panel className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(15,25,35,0.55)" }}>
      <div className="rounded-xl p-7 flex flex-col gap-4" style={{ background: "#ffffff", minWidth: 340, boxShadow: "0 8px 30px rgba(0,0,0,0.25)" }}>
        <h2 className="text-xl font-semibold" style={{ color: "var(--primary)" }}>Staff</h2>
        <p>{staff ? <>Signed in as <b>{staff.username}</b>{staff.mode === "master" ? " (Master administrator)" : " (Staff)"}</> : "Signed in"}</p>
        {message && <p role="alert" style={{ color: "#b42318" }}>{message}</p>}
        <div className="flex gap-3 justify-end">
          <button type="button" onClick={() => setOpen(false)} className="px-5 py-2.5 rounded-md" style={{ border: "1.5px solid var(--border)", color: "var(--primary)" }}>Close</button>
          <button type="button" disabled={busy} onClick={() => void signOut()} className="px-5 py-2.5 rounded-md font-semibold" style={{ backgroundColor: "var(--primary)", color: "#ffffff" }}>
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
        <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>Ctrl+Shift+L or Esc to close.</p>
      </div>
    </div>
  );
}
