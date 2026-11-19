// ============================================================================
// File:         integration_BV.ts
// Written by:   BV (Blas Vargas); CG (the photo, security camera and sign-out
//               calls brought back from the earlier compiled form, tagged [CG])
// What it does: Calls from the browser to the local scan API.
// Team-written: Logic written by BV (Blas Vargas); CG (lines tagged [CG]).
//               Outside code is used only through the third-party sources
//               listed below.
// Third-party:  none (standard library only)
// ============================================================================
/** Local API adapters. No payment details or document images are persisted by the UI. */

// [BV] every call to our own server goes through here: marks it as coming from the kiosk screen, and a 401 (signed out)
// sends the screen back to staff sign-in
export async function kioskFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  headers.set("X-Kiosk-Request", "1");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("/api/auth/") && typeof window !== "undefined") {
    window.dispatchEvent(new Event("kiosk-session-expired"));
  }
  return response;
}

export interface ScanResult {
  success: boolean;
  fields: Record<string, string>;
  missing: string[];
  notes: string[];
  recommendation: string;
  confidence?: Record<string, number>;  // [CG] 0-1 per field, from the insurance reader; the license barcode has none (it's exact)
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

// [CG] the front of the license, saved before the barcode scan so it can go on the Monday record. Brought back from the
// earlier compiled form (BV's design); the Nov 17 rebuild from src/ had lost it
export async function saveLicenseFront(signal: AbortSignal): Promise<void> {
  const response = await kioskFetch("/api/camera/license-front", { method: "POST", signal });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.saved) throw new Error(result.error || "The front photo could not be saved. Please try again.");
}

// [CG] asks the security Pi for its "license_scan" photo when the license scanner opens. Also brought back from the earlier
// compiled form. A failure only means one missing security photo, so it never stops the customer
let securityPhotoPending = false;  // one request at a time, like the earlier form
export async function requestSecurityPhoto(): Promise<void> {
  if (securityPhotoPending) return;
  securityPhotoPending = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 50000);
  try {
    const response = await kioskFetch("/api/security/front-capture", { method: "POST", headers: { "X-Kiosk-Security": "1" }, signal: controller.signal });
    if (!response.ok) console.warn("Security photo request could not be queued.");
  } catch {
    console.warn("Security photo request could not be queued.");
  } finally {
    clearTimeout(timer);
    securityPhotoPending = false;
  }
}

// [CG] staff session: who is signed in, and signing out. Brought back from the earlier compiled form
export interface StaffUser { username: string; mode?: string }

export async function staffSession(signal?: AbortSignal): Promise<StaffUser | null> {
  const response = await kioskFetch("/api/auth/session", { signal });
  if (!response.ok) return null;
  const result = await response.json().catch(() => ({}));
  return result.user || null;
}

export async function signOut(): Promise<void> {
  const response = await kioskFetch("/api/auth/logout", { method: "POST", signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error("Sign-out failed. Please try again.");
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
