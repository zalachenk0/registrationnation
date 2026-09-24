# Registration Nation: user flow and demo notes

The diagram follows the current application. It shows five form steps followed by a confirmation screen. Staff sign-in happens before the customer journey. The camera, document extraction, payment and printer integration are all implemented and connected to real (test-mode, for payment) backends.

## Before the customer starts: staff sign-in

An operator signs in to open the kiosk. The customer then starts directly on Personal Information. There is no separate customer account creation or welcome-screen action in this version. Starting the next customer clears the customer form but keeps the operator signed in.

## 1. Personal information

The customer places the driver's license under the camera and selects **Capture & Scan**, or chooses **Upload an image instead**. The license reader decodes the license barcode. For a camera demonstration, position the barcode side so it is readable.

On a successful read, the app fills available name, date of birth and address fields. It also carries the license number, issuing state, expiration and class into the next step. The customer reviews the extracted personal fields and manually enters email and phone. Missing or incorrect extracted values can be corrected.

Demo narration: "We begin by scanning the driver's license. The kiosk fills the available personal and license information, so the customer only needs to add their contact details and check the result."

## 2. Driver's license details

The customer confirms the license number, issuing state, expiration date and license class. These fields use the same scan from step 1. A second license scan is not part of the normal journey. Each field remains editable.

Demo narration: "The same scan also fills the license details. This step gives the customer a chance to check them before continuing."

## 3. Insurance and vehicle

The customer captures or uploads proof of insurance. Optical character recognition reads the document, and the current app maps available vehicle year, make, model, VIN and insurance expiration into the form. Extraction depends on document readability and content.

The customer reviews those values and completes the license plate, insurance provider, policy number, policy holder, start date and coverage type. Agent name and phone are optional. Do not describe the entire insurance form as automatically completed: the current scan integration only fills the fields listed above.

Demo narration: "Next, the insurance scan fills available vehicle information and the insurance expiration. The customer checks those values and completes the remaining policy details."

## 4. Review and confirm

The app displays personal, license, vehicle and insurance information together. The customer can select **Edit** beside a field and correct it directly. The Back button also allows movement to the previous form step. Once satisfied, the customer selects **Proceed to Payment**.

This is a customer review step, not an external identity, DMV or insurance verification. The current navigation does not block progress when required-looking fields are empty.

Demo narration: "Before the final step, the customer sees the information in one place and can correct individual fields without restarting the process."

## 5. Payment and print submission

The payment screen displays a fixed total of **$71.00**: $51.75 registration renewal, $4.75 state processing, $11.50 county road and bridge, and $3.00 service fee. These are the app's configured amounts.

The screen connects to a card reader automatically (a Stripe Terminal simulated reader for this walkthrough — no typing or physical card required) and shows a status of connecting, then ready. Selecting **Pay $71.00 & Print** runs an actual Stripe charge, in Stripe's test mode, through that reader, then generates a confirmation number and submits the registration mockup to the print service. The button shows **Processing Payment...** while the charge runs, then **Sending to Printer...** while the print job is in flight, and prevents another click while either is pending.

This is a genuine payment gateway call and card authorization — it is real Stripe API activity, just run in test mode so no real card or bank account is involved. Once the physical S700 reader is connected, this same flow runs against a real card with a one-line configuration change (swapping the simulated reader for a real Stripe reader Location) — nothing about the payment logic itself changes.

Demo narration: "This is a real Stripe charge, running in test mode against a simulated card reader. Once our physical reader is connected, this exact flow runs against a real card. After the charge succeeds, we send our registration output to the connected printer."

## Completion and the next customer

When the print service accepts the job, the app shows its completion screen and a confirmation number. The customer can collect the demonstration sticker / receipt. The printer output explicitly identifies itself as a demonstration, not an official registration document.

Print acceptance does not confirm that paper has physically finished printing. The UI's completion wording is stronger than what the backend verifies, so describe this transition as the print job being accepted.

Selecting **Start Next Customer** resets personal, license, insurance and payment state, clears the confirmation and returns to step 1 while retaining the operator session.

Demo narration: "Once the printer accepts the job, the kiosk displays a confirmation number and logs the renewal to our tracking board. We collect the demo output, then select Start Next Customer to clear the form for the next person."

## Recovery paths worth explaining

- **Unreadable scan:** The app displays a scan error. **Try Again** opens the image picker, allowing another image to be supplied. After a successful scan, **Rescan** returns the scan component to its initial capture/upload choices.
- **Incorrect extracted value:** The customer edits it in the form or on the Review screen. A scan reads document data; it does not guarantee the data is correct or current.
- **Card declined or reader error:** The app shows the error and stays on Payment. The customer (or operator) can retry; a fresh attempt is charged once the retry succeeds.
- **Print submission failure (after a successful charge):** The app stays on Payment and displays the error. A retry re-attempts printing only — it reuses the already-successful charge rather than billing the card again. A failed print request does not lead to the completion screen.
- **Incomplete form:** Fields show required markers, but the current Continue and final-action handlers do not enforce completion. The diagram represents the intended customer walkthrough, not mandatory validation gates.

## Suggested live demo sequence

1. Sign in before starting the customer walkthrough.
2. Scan a test license and point out the personal fields that populate.
3. Enter test contact details, continue, and review the license details.
4. Scan a test insurance document and complete remaining vehicle/policy fields.
5. On Review, intentionally correct one field to demonstrate customer control.
6. Let the reader connect (watch the status change to "ready"), then select **Pay $71.00 & Print**.
7. Show the confirmation screen and the physical demonstration output.
8. Select Start Next Customer to demonstrate reset.

Use the PNG as a full-slide image in a 16:9 deck. The SVG preserves crisp text and lines when resized in slide software that supports SVG.

## Source basis

- `integrated-kiosk/src/App.tsx`: screen order, scanner mapping, edits, payment step, print submission and session reset.
- `integrated-kiosk/src/payment.ts`: Stripe Terminal SDK connection and charge flow.
- `integrated-kiosk/SCANNER_SETUP.md`: camera capture, license barcode decoding, insurance OCR, and payment configuration.
- `integrated-kiosk/printer_service.py`: demo output content and print-job acceptance behavior.
