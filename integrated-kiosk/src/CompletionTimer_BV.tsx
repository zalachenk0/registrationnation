// ============================================================================
// File:         CompletionTimer_BV.tsx
// Written by:   BV (Blas Vargas)
// What it does: Countdown on the final screen that resets the form for the next
//               customer.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  React
// ============================================================================
import { useEffect, useRef, useState } from "react";

export function CompletionTimer({ onReset }: { onReset: () => void }) {
  const [remaining, setRemaining] = useState(20);
  const [paused, setPaused] = useState(false);
  const pauseRef = useRef(false);
  const resetRef = useRef(onReset);
  resetRef.current = onReset;
  useEffect(() => {
    if (paused) return;
    const deadline = Date.now() + 20000;
    const timer = setInterval(() => {
      if (pauseRef.current) return;
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(seconds);
      if (seconds === 0) { clearInterval(timer); resetRef.current(); }
    }, 200);
    return () => clearInterval(timer);
  }, [paused]);
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <p role="status" className="text-sm">
        {paused ? "Reset paused. Take your time collecting your sticker and receipt." : `A new customer session starts in ${remaining} seconds.`}
      </p>
      <button type="button" onClick={() => {
        pauseRef.current = !paused;
        setPaused(!paused);
        if (paused) setRemaining(20);
      }} className="px-8 py-3 rounded-lg text-sm font-semibold" style={{ backgroundColor: "var(--primary)", color: "#fff" }}>
        {paused ? "Resume 20-second countdown" : "Wait, I’m still here"}
      </button>
    </div>
  );
}
