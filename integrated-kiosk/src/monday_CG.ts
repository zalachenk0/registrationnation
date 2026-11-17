import { kioskFetch } from "./integration_BV";
// ============================================================================
// File:         monday_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Browser side call that saves the finished renewal to Monday.com
//               through our server.
// Team-written: All logic written by CG (Cristian Gonzalez), except the lines
//               tagged [3RD PARTY].
// Third-party:  Monday.com (through our server and monday_service_CG.py).
// ============================================================================
// monday_CG.ts, browser side
//
// saves a finished renewal to our monday.com crm board
// just like payment_CG.ts this never talks to monday directly, it asks our flask server (scanner_api_BV_CG.py)
// because the monday api token is a secret and can only live on the server

// our design
// either it saved (maybe with a few fields skipped) or here's why it didn't
type MondayResult =
  | { success: true; itemId: string; warnings: string[] }
  | { success: false; error: string };

// give it the row's name and everything the form knows, the server decides which fields actually go to monday
export async function createMondayRecord(
  itemName: string,
  fields: Record<string, string | number>
): Promise<MondayResult> {
  try {
    const response = await kioskFetch("/api/monday/create_record", {   // our route, not monday's
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ itemName, fields }),
    });
    const data = await response.json().catch(() => ({}));  // in case the server sent back something that isn't json
    if (!response.ok) {
      throw new Error(data.error || `Monday.com request failed (${response.status}).`);
    }
    return { success: true, itemId: data.itemId, warnings: data.warnings || [] };
  } catch (error) {
    // our design - a monday problem should never look like the payment failed, so the caller decides what to show
    console.error("createMondayRecord failed:", error);
    const message = error instanceof Error ? error.message : "Could not save to Monday.com.";
    return { success: false, error: message };
  }
}
