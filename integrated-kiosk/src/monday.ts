// monday.ts
//
// Logs a completed renewal to the company's Monday.com board. Same
// pattern as payment.ts: one function, called after a payment
// succeeds, that returns whether it worked.

type MondayResult =
  | { success: true; itemId: string }
  | { success: false; error: string };

export async function createMondayRecord(
  itemName: string,
  fields: Record<string, string | number>
): Promise<MondayResult> {
  try {
    const response = await fetch("/api/monday/create_record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ itemName, fields }),
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Could not create the Monday.com record.");
    }

    return { success: true, itemId: data.itemId };
  } catch (error) {
    // A Monday.com problem shouldn't look like the payment failed -
    // the payment already went through by the time this runs. Log it
    // and let the caller decide whether to show anything.
    console.error("createMondayRecord failed:", error);
    const message = error instanceof Error ? error.message : "Logging failed.";
    return { success: false, error: message };
  }
}
