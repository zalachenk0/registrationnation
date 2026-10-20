# Integration details

Source: registrationnation-main (1).zip supplied by the user; latest relevant
scanner code is in kiosk_scanners/. The Pi's existing camera and printing adapters
are retained. Unrelated Monday/desktop applications are not required by this kiosk.

Payments: the group's Python Stripe PaymentIntent creation/capture flow has been
adapted from card_present/Terminal to card/Stripe Payment Element. Server amount
is 11425 cents. The backend retains a SQLite reference-to-intent ledger and
idempotency keys. It checks the test environment, amount, currency, reference,
and successful capture before printing. Keys come from .env. Card details are
handled by Stripe's hosted fields, not Python, React state, or application logs.
The publishable key is returned to the browser; the secret key stays on the server.
Live keys are intentionally rejected. The S700 Terminal reader is the primary method when
STRIPE_LOCATION_ID (or STRIPE_SIMULATED) is set; the typed card is the backup. See S700_AND_MONDAY.md.

The customer completion timer starts only after successful payment and CUPS
acceptance. A failed print preserves the paid state for retry. Pausing the timer
is indefinite until resumed. Reset clears current customer form state and payment
reference and destroys the card fields while preserving the staff session.

OCR: cheap passes over priority/document candidates precede expensive fallback
variants; deadline and per-call timeouts remain. High-confidence complete results
stop early. Existing insurance debug saving and no-VIN vehicle-year correction
are retained. Real camera benchmarks remain to be done.

Local tests: 43 Python tests, 13 JS/React tests, TypeScript, production build, and
browser flow with synthetic inputs/simulated payment and printer. Fixtures and
automated tests are included. Local browser mocking harness is NOT shipped.
No external Stripe charges, camera captures, or printer jobs were performed.

Raspberry Pi installation/run commands and migration details: INSTALL_AND_RUN.md.
