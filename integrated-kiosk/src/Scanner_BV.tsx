// ============================================================================
// File:         Scanner_BV.tsx
// Written by:   BV (Blas Vargas)
// What it does: Camera preview and scan result pieces of the scan window.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  React
// ============================================================================
import { useEffect, useRef, useState } from "react";

export interface ScanResult {
  success: boolean;
  fields: Record<string, string>;
  missing: string[];
  notes: string[];
  recommendation: string;
}

function CameraView({ kind, capture, close }: { kind: string; capture: () => void; close: () => void }) {
  const image = useRef<HTMLImageElement>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let currentUrl = "";
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let delay = 200;
      try {
        const response = await fetch("/api/camera/frame.jpg", { cache: "no-store", signal: controller.signal });
        if (!response.ok) {
          const detail = await response.json();
          throw new Error(detail.error || "Camera unavailable.");
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        const next = URL.createObjectURL(blob);
        if (image.current) image.current.src = next;
        if (currentUrl) URL.revokeObjectURL(currentUrl);
        currentUrl = next;
        setReady(true);
        setError("");
      } catch (err) {
        if (controller.signal.aborted) return;
        delay = 1500;
        setReady(false);
        if (image.current) image.current.removeAttribute("src");
        setError(err instanceof Error ? err.message : "Camera unavailable.");
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, delay);
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); if (currentUrl) URL.revokeObjectURL(currentUrl); };
  }, []);
  return <div role="dialog" aria-modal="true" aria-label="Document camera" className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,.85)" }}>
    <div className="w-full max-w-2xl flex flex-col gap-4 text-white">
      <div className="flex justify-between items-center"><h2 className="text-xl">{kind === "license" ? "Show the BACK of your driver's license" : "Position your insurance card or form"}</h2>
        <button aria-label="Close camera" onClick={close} className="px-4 py-2">Close</button></div>
      <p className="text-sm">Keep the document upright. Move it closer until the small text is sharp, then wait for autofocus.</p>
      <div className="relative overflow-hidden rounded-xl bg-black">
        <img ref={image} alt="Live Arducam preview" className="w-full" style={{ minHeight: ready ? undefined : 240 }} />
        {ready && <div className="absolute inset-0 flex items-center justify-center pointer-events-none"><div style={{ width: kind === "license" ? "80%" : "90%", height: kind === "license" ? "70%" : "90%", border: "2px dashed #f28b42", borderRadius: 12 }} /></div>}
        {!ready && <p role="status" className="absolute inset-0 flex items-center justify-center p-8 text-center">{error || "Starting the Pi camera…"}</p>}
      </div>
      <div className="flex gap-3"><button onClick={close} className="flex-1 border rounded-lg py-3">Cancel</button>
        <button disabled={!ready} onClick={capture} className="flex-[2] rounded-lg py-3 font-semibold disabled:opacity-40" style={{ background: "var(--accent)" }}>Capture &amp; Scan</button></div>
      <p className="text-xs">{kind === "license" ? "The PDF417 barcode supplies the fields. A front photo is not required." : "The whole camera frame is captured. Keep other documents outside the view."}</p>
    </div>
  </div>;
}

export default function ScanPrompt({ title, subtitle, docType, scanned, onScan }: {
  title: string; subtitle: string; docType: "license" | "insurance"; scanned: boolean; onScan: (result: ScanResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const controller = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  useEffect(() => () => controller.current?.abort(), []);
  async function scan(file?: File) {
    if (busyRef.current) return;
    busyRef.current = true;
    setOpen(false); setBusy(true); setError("");
    const abort = new AbortController();
    controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 120000);
    try {
      const body = file ? new FormData() : undefined;
      if (file && body) body.append("image", file);
      const response = await fetch(file ? `/api/scan/${docType}` : `/api/camera/scan/${docType}`, { method: "POST", body, signal: abort.signal });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || data.recommendation || "Could not read this document.");
      if (abort.signal.aborted) return;
      onScan(data); setResult(data);
    } catch (err) {
      setError(abort.signal.aborted ? "Scan cancelled or timed out. Wait for processing to finish, then retry or enter the fields manually." : err instanceof Error ? err.message : "Scanner unavailable.");
    } finally { clearTimeout(timeout); busyRef.current = false; setBusy(false); }
  }
  return <>
    {open && <CameraView kind={docType} capture={() => void scan()} close={() => setOpen(false)} />}
    <div className="rounded-xl p-6 flex flex-col gap-3" style={{ border: "2px dashed var(--border)", background: scanned ? "#f0f7ee" : "var(--card)" }}>
      <h3 className="font-semibold">{title}</h3><p className="text-sm">{subtitle}</p>
      {busy ? <div role="status" className="text-sm">Reading your document… Insurance OCR can take about a minute on the Pi.
        <button onClick={() => controller.current?.abort()} className="block underline mt-2">Cancel and enter manually</button></div> :
        <div className="flex flex-wrap items-center gap-4"><button onClick={() => setOpen(true)} className="rounded-lg px-6 py-3 font-semibold text-white" style={{ background: "var(--accent)" }}>{scanned ? "Rescan document" : "Open Scanner"}</button>
          <label className="cursor-pointer underline text-sm">Upload a photo instead<input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void scan(file); }} /></label></div>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {result && <div role="status" className="text-sm"><p>{result.recommendation} Existing entries are kept; clear a field before rescanning to replace it.</p>
        {!!result.missing.length && <p className="mt-1">Not read: {result.missing.map(k => k.replace(/_/g, " ")).join(", ")}.</p>}
        {result.notes.map((note, i) => <p key={i} className="mt-1">{note}</p>)}</div>}
      <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>Scanning is optional. All fields remain editable.</p>
    </div>
  </>;
}
