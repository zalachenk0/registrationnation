export async function kioskFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  headers.set("X-Kiosk-Request", "1");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("/api/auth/") && typeof window !== "undefined") {
    window.dispatchEvent(new Event("kiosk-session-expired"));
  }
  return response;
}

// ============================================================================
// File:         integration_BV.ts
// Written by:   BV (Blas Vargas)
// What it does: Calls from the browser to the local scan API.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  none (standard library only)
// ============================================================================
/** Local API adapters. No payment details or document images are persisted by the UI. */
export interface ScanResult {
  success: boolean;
  fields: Record<string, string>;
  missing: string[];
  notes: string[];
  recommendation: string;
}

export function fillBlank<T extends object>(current: T, incoming: Partial<Record<keyof T, string | undefined>>): T {
  const next = { ...current };
  for (const key of Object.keys(incoming) as (keyof T)[]) {
    const value = incoming[key];
    if (!String(current[key] ?? "").trim() && value?.trim()) next[key] = value.trim() as T[keyof T];
  }
  return next;
}

export async function requestScan(kind: "license" | "insurance", signal: AbortSignal): Promise<ScanResult> {
  const response = await kioskFetch(`/api/camera/scan/${kind}`, { method: "POST", signal });
  const result = await response.json();
  if (!response.ok || !result.success || !result.fields || !Object.values(result.fields).some(value => typeof value === "string" && value.trim())) {
    throw new Error(result.error || result.recommendation || "No readable fields found. Reposition the document and try again, or enter the fields manually.");
  }
  return result;
}

export interface PrintStatus {
  state: string;
  email_state: string;
}

export async function printStatus(reference: string, signal: AbortSignal): Promise<PrintStatus> {
  const response = await kioskFetch(`/api/print/status/${encodeURIComponent(reference)}`, { cache: "no-store", signal });
  if (!response.ok) throw new Error("Could not check printer status.");
  return response.json();
}

export async function submitPrint(payload: Record<string, string>): Promise<PrintStatus> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await kioskFetch("/api/print/registration", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.error || "The print queue did not accept the job.");
    return result;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("The print request timed out. Check the printer queue before retrying; the same reference will be reused.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
