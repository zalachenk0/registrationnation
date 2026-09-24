# Monday.com integration — saved for a future demo

This is fully working code, just disconnected from the current app on
purpose. Nothing here needs to be rewritten to bring it back — only
reconnected.

## To bring it back

1. **Copy these two files back into the project:**
   - `monday_service.py` → `integrated-kiosk/monday_service.py`
   - `monday.ts` → `integrated-kiosk/src/monday.ts`

2. **In `scanner_api.py`, add back the import:**
   ```python
   from monday_service import MondayError, create_monday_record
   ```
   (Right after the `payment_service` import line.)

3. **In `scanner_api.py`, add back the route**, right after the
   `/api/payment/capture_intent` route and before `@app.get("/")`:
   ```python
   @app.post("/api/monday/create_record")
   def monday_create_record():
       payload = request.get_json(silent=True) or {}
       item_name = payload.get("itemName")
       fields = payload.get("fields") or {}

       try:
           item_id = create_monday_record(item_name, fields)
           return jsonify(success=True, itemId=item_id)
       except MondayError as exc:
           return jsonify(error=str(exc)), 400 if "required" in str(exc) else 500
   ```

4. **In `App.tsx`, add back the import:**
   ```tsx
   import { createMondayRecord } from "./monday";
   ```

5. **In `App.tsx`'s `handlePaymentAndPrint`**, right after
   `setPendingPaymentIntentId(null); // this renewal is fully done`,
   add back:
   ```tsx
   createMondayRecord(`${license.licensePlate} renewal`, {
     plate: license.licensePlate,
     registrant: `${personal.firstName} ${personal.lastName}`.trim(),
     amount: RENEWAL_FEE_CENTS,
     confirmationNumber: confirmation,
     paymentIntentId,
   }).then((mondayResult) => {
     if (mondayResult.success) {
       console.log(`Logged to Monday.com: item ${mondayResult.itemId}`);
     } else {
       console.warn(`Monday.com logging skipped: ${mondayResult.error}`);
     }
   });
   ```

6. **Add `requests>=2.32,<3` back to `requirements-scanner.txt`** —
   `monday_service.py` needs it and it isn't listed while this is
   disconnected.

7. **Add `MONDAY_API_TOKEN=` and `MONDAY_BOARD_ID=` back to
   `.env.example`**, and fill in real values in your own `.env`.

8. **Fill in the real column IDs** in `monday_service.py`'s
   `MONDAY_COLUMN_MAP` — this is the one piece that was never finished,
   since it depends on your actual Monday.com board's column setup.

That's the whole reconnection — nothing else in the project changes.
