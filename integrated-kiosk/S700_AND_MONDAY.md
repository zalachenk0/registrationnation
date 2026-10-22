# S700 card reader and Monday.com CRM

## What this adds

1. **The Stripe S700 card reader is the primary way to pay.** The typed-card form is the backup:
   on the payment screen, "Reader not working? Enter the card manually instead".
2. **Every finished renewal is saved to the Monday.com CRM board** (one new row, shown on the
   success screen as "CRM record (Monday.com): Saved ✓", with a Retry button if Monday is down).

Nothing else about the form changes. With no reader configured, the kiosk behaves exactly like
the typed-card version.

## Settings (`.env`)

| Setting | Meaning |
|---|---|
| `STRIPE_LOCATION_ID=tml_...` | Use the real S700. The Location id from Stripe Dashboard > Terminal > Locations. |
| `STRIPE_SIMULATED=1` | Use Stripe's built-in fake reader (no hardware). Wins over the location. Good for rehearsing. |
| both blank | No reader: typed card only. |
| `MONDAY_API_TOKEN`, `MONDAY_BOARD_ID`, `MONDAY_GROUP_ID` | Monday.com. The token is secret and only ever lives in `.env`. |

A mistyped `STRIPE_LOCATION_ID` never breaks payment: the reader is turned off, the typed card
still works, and the screen says why.

## Receipt email

The email the customer typed is given to Stripe just before their payment is captured, and Stripe emails its receipt
at that moment. This works the same for the S700 reader and the typed card. The success screen shows
"Stripe will email a receipt to ..." and a "Receipt email" row. If the address doesn't look valid, or Stripe can't
attach it, the payment still completes and the screen says no receipt email could be set up.

**Test mode:** Stripe does not send receipts automatically for payments made with test keys, except when the email belongs to
the Stripe account owner or a verified team member. To test real delivery, type your own Stripe account email into the kiosk.
For any other address, open the payment in the Stripe Dashboard and click **Send receipt** (Receipt history section).

## Emailed Texas Tag & Title receipt

After a payment is confirmed, the kiosk emails the customer the Texas Tag & Title receipt: page 2 of the print-out, as a one-page
PDF **without the signature block** (nobody can sign an email). This is separate from Stripe's own receipt email above.

- It goes out as soon as the payment is confirmed, so it is sent even if the printer is down.
- One email per payment. A retry or double click can never send it twice.
- A mail problem never blocks a payment or a print. The success screen shows **Receipt PDF by email: Sent / Not sent**, and a
  **Retry sending receipt** button when it failed.
- Only a payment Stripe has confirmed can trigger an email. The kiosk stores only a fingerprint of the address, not the address.

**Set it up (one time per kiosk):**

1. In `.env`, set `SMTP_USERNAME` (already `registrationnation@texastagandtitle.com` in `.env.example`) and `SMTP_PASSWORD`.
   An existing `.env` does not get new lines automatically, so add them by hand. **Never share or commit the password.**
2. For a Google account the password must be an **App Password**, not the normal password. Turn on 2-Step Verification for the account,
   then create an App Password at `myaccount.google.com/apppasswords`. Google shows it as four groups of four letters. Paste it as shown, spaces are fine.
3. Check the login without sending anything: `python3 receipt_email.py check`
   (Windows: `.\.venv\Scripts\python.exe receipt_email.py check`).
4. Send yourself a sample TEST receipt: `python3 receipt_email.py test you@example.com`. Check the inbox and the spam folder.

**If Google rejects the login.** A custom-domain address such as `@texastagandtitle.com` is usually Google Workspace, and Google
Workspace may not allow App Passwords. If the App Passwords page does not appear, or the check says the username or password
was rejected, ask whoever administers the Workspace, or use a different email service. Nothing in the code changes. Set
`SMTP_HOST`, `SMTP_PORT` (587, or 465 for a secure connection from the start), `SMTP_USERNAME`, `SMTP_PASSWORD`, and `RECEIPT_FROM`
if the sending address differs from the login. `RECEIPT_FROM` must be an address the login is allowed to send as.

Gmail limits how many emails an account can send per day (roughly 500 for a personal account and 2,000 for Workspace).
That is plenty for a kiosk.

## Setting up the real S700 (one time)

1. In the Stripe Dashboard (test mode) create a **Location** (Terminal > Locations) and copy its id (`tml_...`).
2. Power on the S700 and connect it to the **same network as the kiosk** (campus Wi-Fi often blocks
   devices from reaching each other, so a small router or phone hotspot for both is safer).
3. On the reader open its settings and tap **Generate pairing code**, then in the Dashboard go to
   Terminal > Readers > Register and enter the code, choosing your Location.
4. Put the Location id in `.env` as `STRIPE_LOCATION_ID`, restart the backend, reload Chromium.
5. In test mode the reader only accepts Stripe's **physical test cards** (Dashboard > Terminal > Shop).

**Chromium permission:** Chrome/Chromium 142 and later ask permission before a page may talk to a device
on the local network, such as the reader. Click **Allow** once. If the kiosk can't show the prompt, copy
`chromium-local-network-policy.json` to `/etc/chromium/policies/managed/` (older Pi OS:
`/etc/chromium-browser/policies/managed/`), restart Chromium, and check `chrome://policy`.
This is Stripe's documented setting for the Terminal JavaScript SDK, but it has not been tried on this Pi.

## How paying behaves

- The reader is the default screen for every customer, even after a customer used the backup.
- Pay starts a **card_present** payment; the reader asks for the card; Stripe approves; the server captures.
- While the reader waits for a card, **Cancel card reader payment** stops it.
- Switching to the typed card is blocked while a payment is in progress. Switching is safe otherwise:
  an unfinished reader payment is **canceled first**, then a typed-card payment is created.
- A payment that is already approved or captured is never replaced. Choosing the other method just
  finishes that same payment, so a customer can't be charged twice.
- A reader payment where the card was read but never approved restarts fresh (nothing was approved).
- If the printer fails after payment, the button becomes **Retry Printing** and the payment methods are locked.

## Monday.com setup

1. Put `MONDAY_API_TOKEN` and `MONDAY_BOARD_ID` (and `MONDAY_GROUP_ID=group_mm88283z` for the Kiosk Renewals group) in `.env`.
2. `python3 monday_service.py columns` lists the board's columns. `python3 monday_service.py test` adds one `[TEST]` row (delete it afterward).
3. The **Renewal Status** label `Completed at kiosk` must exist on the board, spelled exactly like that,
   or every save is rejected and the success screen shows "Not saved".
4. Only the fields listed in `COLUMN_MAP` (top of `monday_service.py`) are sent. Driver's license number, date of
   birth and the full VIN are never sent.

## Updating the Pi

Replace the folder as `INSTALL_AND_RUN.md` describes (keep `.env` and `runtime/`), then run `bash setup-pi.sh`
again: this version needs two Python packages the old one did not install (`pypdf` for printing, `requests` for Monday).
The built `dist/` folder is included.

## What was tested, and what was not

Tested with stand-ins for Stripe, the reader, Monday, the printer and the camera: 70 Python tests (67 pass; the
3 failures are old stale ones, listed below), 13 JavaScript tests, TypeScript, the production build, and a browser run of
every switching and failure case above.
**Not tested:** a physical S700, real Stripe or Monday servers, the Pi camera and printer, Chromium's local-network prompt,
and whether Stripe's SDK accepts the kiosk's `http://127.0.0.1` page for a physical reader (Stripe's checklist asks for HTTPS).
Stripe now recommends its *server-driven* integration for the S700; this build uses the JavaScript SDK, which Stripe still documents.

Three older tests fail because they expect one printed page and unscaled OCR debug images, but the code now prints
two pages and upscales: `test_pdf_and_print_idempotency`, `test_paid_print_retry_and_receipt`,
`test_default_saves_exact_original_and_best_ocr_in_separate_scan_folders`. They failed before these changes too.

## Files

New: `src/terminal.ts`, `src/TerminalPayment.tsx`, `src/monday.ts`, `monday_service.py`, `chromium-local-network-policy.json`,
`tests/test_terminal_payments.py`, `tests/test_monday.py`, this file.
Changed: `payment_service.py`, `scanner_api.py`, `src/payment.ts`, `src/CardPayment.tsx`, `src/App.tsx`, `package.json`,
`pnpm-lock.yaml`, `requirements-pi.txt`, `requirements-desktop.txt`, `check-pi.sh`, `.env.example`,
`tests/test_payments.py` (one test now rejects a bank-account payment instead of a reader payment), and small wording fixes in four guides.
