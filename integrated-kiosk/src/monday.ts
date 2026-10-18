// monday.ts, browser side
//
// saves a finished renewal to our monday.com crm board
// just like payment.ts this never talks to monday directly, it asks our flask server (scanner_api.py)
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
    const response = await fetch("/api/monday/create_record", {   // our route, not monday's
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
