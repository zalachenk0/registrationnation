// ============================================================================
// File:         StartScreen_SL.tsx
// Written by:   SL; CG (payment line reworded, tagged [CG])
// What it does: Welcome screen shown after staff sign in and between
//               customers. The customer taps Start to begin a renewal.
// Team-written: All logic written by SL, except the line tagged [CG].
// Third-party:  React
// ============================================================================
import type { ReactNode } from "react";
import logoUrl from "./imports/RN_Logo.png";

const NEEDS = [
  {
    label: "Your driver’s license",
    icon: (
      <svg width="40" height="26" viewBox="0 0 92 58" fill="none" aria-hidden="true">
        <rect x="2" y="2" width="88" height="54" rx="5" stroke="currentColor" strokeWidth="5" />
        <circle cx="22" cy="20" r="7" stroke="currentColor" strokeWidth="5" />
        <path d="M9 42c1.5-8 6.5-12 13-12s11.5 4 13 12" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
        <path d="M48 16h28M48 29h28M48 42h18" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    label: "Proof of insurance, on paper or your phone",
    icon: (
      <svg width="24" height="32" viewBox="0 0 56 72" fill="none" aria-hidden="true">
        <rect x="3" y="3" width="50" height="66" rx="4" stroke="currentColor" strokeWidth="5" />
        <path d="M14 18h28M14 32h28M14 46h28" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    label: "A card ready for payment",  // [CG] no amount here, the payment step shows it
    icon: (
      <svg width="40" height="28" viewBox="0 0 40 28" fill="none" aria-hidden="true">
        <rect x="1.5" y="1.5" width="37" height="25" rx="3.5" stroke="currentColor" strokeWidth="2.5" />
        <path d="M1.5 9h37" stroke="currentColor" strokeWidth="4" />
        <path d="M7 20h9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    ),
  },
];

export default function StartScreen({ onStart, staffBar }: { onStart: () => void; staffBar?: ReactNode }) {
  return (
    <div className="kiosk-ui min-h-screen flex flex-col" style={{ backgroundColor: "var(--background)" }}>
      {staffBar}
      <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <img src={logoUrl} alt="Registration Nation" className="h-12 w-auto object-contain" />
          <div className="text-right">
            <p className="text-xs font-semibold tracking-wider uppercase" style={{ color: "var(--muted-foreground)" }}>Texas Tag & Title</p>
            <p className="text-sm font-medium" style={{ color: "var(--primary)" }}>Vehicle Registration Renewal</p>
          </div>
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-4xl rounded-3xl px-8 py-14 flex flex-col items-center text-center"
          style={{ backgroundColor: "var(--card)", border: "1.5px solid var(--border)" }}>
          <p className="text-sm font-semibold uppercase tracking-widest" style={{ color: "var(--accent)" }}>Welcome</p>
          <h1 className="mt-4 text-6xl leading-tight max-w-3xl" style={{ color: "var(--primary)" }}>
            Renew your vehicle registration
          </h1>
          <p className="mt-6 text-2xl" style={{ color: "var(--foreground)" }}>Before you start, have these ready:</p>

          <ul className="mt-10 w-full max-w-xl flex flex-col gap-4 text-left">
            {NEEDS.map(item => (
              <li key={item.label} className="flex items-center gap-5 rounded-2xl px-6 py-4" style={{ backgroundColor: "#f3f6fa" }}>
                <span className="w-16 h-14 shrink-0 rounded-xl flex items-center justify-center"
                  style={{ backgroundColor: "#e3e9f1", color: "var(--primary)" }}>
                  {item.icon}
                </span>
                <span className="text-xl" style={{ color: "var(--foreground)" }}>{item.label}</span>
              </li>
            ))}
          </ul>

          <button type="button" onClick={onStart}
            className="mt-12 w-full max-w-md py-6 rounded-xl text-3xl font-bold transition-colors duration-150"
            style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#d95c1a"; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--accent)"; }}>
            Start
          </button>
          <p className="mt-5 text-lg" style={{ color: "var(--muted-foreground)" }}>Tap Start to begin.</p>
        </div>
      </main>
    </div>
  );
}
