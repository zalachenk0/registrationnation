// ============================================================================
// File:         SignaturePad_SL.tsx
// Written by:   SL
// What it does: Box the customer signs in with a finger. Hands back the
//               signature as a PNG data URL (cropped to the ink) for the receipt.
// Team-written: All logic written by SL.
// Third-party:  React (browser canvas and pointer events).
// ============================================================================
import { useEffect, useRef } from "react";

const INK = "#0f1f33";
const PAD = 12;          // blank space kept around the ink when cropping, in CSS px
const MAX_WIDTH = 900;   // exported image is scaled down to at most this many px wide

export default function SignaturePad({ value, onChange }: { value: string; onChange: (png: string) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef<{ id: number; last: { x: number; y: number }; mid: { x: number; y: number } } | null>(null);
  const bounds = useRef<{ minX: number; minY: number; maxX: number; maxY: number } | null>(null);

  // size the canvas to its box at the screen's pixel density; clears it, so only when empty
  const fit = () => {
    const c = canvas.current;
    if (!c) return;
    const ratio = window.devicePixelRatio || 1;
    const { width, height } = c.getBoundingClientRect();
    c.width = Math.round(width * ratio);
    c.height = Math.round(height * ratio);
    const ctx = c.getContext("2d")!;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = INK; ctx.fillStyle = INK; ctx.lineWidth = 3;
    bounds.current = null;
  };
  useEffect(() => {
    fit();
    if (value) {  // coming back to this step: put the saved signature back in the box
      const image = new Image();
      image.onload = () => {
        const c = canvas.current;
        if (!c || bounds.current) return;
        const ratio = window.devicePixelRatio || 1;
        const box = c.getBoundingClientRect();
        const scale = Math.min(1 / ratio, box.width / image.width, box.height / image.height);
        const w = image.width * scale, h = image.height * scale;
        const x = (box.width - w) / 2, y = (box.height - h) / 2;
        c.getContext("2d")!.drawImage(image, x, y, w, h);
        bounds.current = { minX: x + PAD, minY: y + PAD, maxX: x + w - PAD, maxY: y + h - PAD };
      };
      image.src = value;
    }
    const resize = () => { if (!bounds.current) fit(); };
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  // parent cleared the value (e.g. a new customer): wipe the ink too
  useEffect(() => { if (!value && bounds.current) fit(); }, [value]);

  const point = (event: React.PointerEvent) => {
    const rect = canvas.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const grow = ({ x, y }: { x: number; y: number }) => {
    const b = bounds.current;
    bounds.current = b ? { minX: Math.min(b.minX, x), minY: Math.min(b.minY, y), maxX: Math.max(b.maxX, x), maxY: Math.max(b.maxY, y) }
      : { minX: x, minY: y, maxX: x, maxY: y };
  };

  const down = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (drawing.current) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const p = point(event);
    drawing.current = { id: event.pointerId, last: p, mid: p };
    const ctx = event.currentTarget.getContext("2d")!;
    ctx.beginPath(); ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2); ctx.fill();  // a tap leaves a dot
    grow(p);
  };
  const move = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drawing.current;
    if (!d || event.pointerId !== d.id) return;
    event.preventDefault();
    const ctx = event.currentTarget.getContext("2d")!;
    const p = point(event);
    const mid = { x: (d.last.x + p.x) / 2, y: (d.last.y + p.y) / 2 };
    ctx.beginPath(); ctx.moveTo(d.mid.x, d.mid.y); ctx.quadraticCurveTo(d.last.x, d.last.y, mid.x, mid.y); ctx.stroke();  // smooth curve through the points
    d.last = p; d.mid = mid;
    grow(p);
  };
  const up = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drawing.current;
    if (!d || event.pointerId !== d.id) return;
    drawing.current = null;
    onChange(exportPng());
  };

  // crop to the ink on a white background (no transparency, prints cleanly)
  const exportPng = () => {
    const c = canvas.current, b = bounds.current;
    if (!c || !b) return "";
    const ratio = c.width / c.getBoundingClientRect().width;
    const x = Math.max(0, (b.minX - PAD) * ratio), y = Math.max(0, (b.minY - PAD) * ratio);
    const w = Math.min(c.width - x, (b.maxX - b.minX + PAD * 2) * ratio), h = Math.min(c.height - y, (b.maxY - b.minY + PAD * 2) * ratio);
    const scale = Math.min(1, MAX_WIDTH / w);
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(w * scale)); out.height = Math.max(1, Math.round(h * scale));
    const ctx = out.getContext("2d")!;
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(c, x, y, w, h, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  };

  const clear = () => { fit(); onChange(""); };

  return (
    <div className="flex flex-col gap-2">
      <div className="signature-pad relative rounded-xl overflow-hidden"
        style={{ border: `2px ${value ? "solid" : "dashed"} ${value ? "var(--primary)" : "var(--border)"}`, backgroundColor: "#fff" }}>
        <canvas ref={canvas} aria-label="Signature box. Sign with your finger." role="img"
          className="block w-full" style={{ height: 200, touchAction: "none", cursor: "crosshair" }}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
        <div className="pointer-events-none absolute left-6 right-6 bottom-12 flex items-end gap-2" aria-hidden="true">
          <span className="text-2xl leading-none" style={{ color: "var(--muted-foreground)" }}>×</span>
          <div className="h-px flex-1" style={{ backgroundColor: "#9aa6b2" }} />
        </div>
        {!value && (
          <p className="pointer-events-none absolute inset-x-0 top-6 text-center text-lg" style={{ color: "var(--muted-foreground)" }}>
            Sign here with your finger
          </p>
        )}
      </div>
      <div className="flex justify-end">
        <button type="button" onClick={clear} disabled={!value}
          className="px-5 py-2.5 rounded-md text-base font-medium disabled:opacity-40"
          style={{ color: "var(--primary)", border: "1.5px solid var(--border)", backgroundColor: "var(--card)" }}>
          Clear and sign again
        </button>
      </div>
    </div>
  );
}
