# Registration Nation — complete Pi kiosk

Updated October 9, 2026. Start with [INSTALL_AND_RUN.md](INSTALL_AND_RUN.md)
for all replacement, setup, key configuration, and startup commands.

- Total: **$114.25 USD**, enforced by the payment backend.
- Secure, editable Stripe card fields replace the simulated Terminal reader.
  This build uses test keys only and does not charge real money.
- Coverage start date and policy type are optional; other required fields remain.
- Payment success plus print-queue acceptance starts a 20-second customer reset.
  “Wait, I'm still here” pauses it; a resume button starts a fresh 20 seconds.
- Latest supplied ZIP's scanner package integrated with the Pi adapters.
  Quick OCR passes try document candidates before expensive fallback passes;
  high-confidence complete results stop early. Review extracted fields before paying.
- Full live camera preview stays uncropped with no frame or moving scan line.
- Insurance originals and OCR debug output save by default in runtime/insurance_debug.
- Built UI included in dist/; no frontend build tools needed on the Pi.

Preserve your old hidden .env and runtime/ when replacing the project. Add the
new STRIPE_PUBLISHABLE_KEY from the same test account as STRIPE_SECRET_KEY.
Reader and location IDs are unused. Camera/printer settings can stay as they are.

Local validation: 43 Python tests, 13 JavaScript/React tests, TypeScript check,
production build, and a browser flow with simulated Stripe/printing. No real
Stripe account, Pi camera, or physical printer was tested here. The supplied
sticker is a demonstration mockup, not official registration. Stripe emails a
receipt to the customer's email when the payment is captured (in test mode only to the
Stripe account owner's address). Existing staff login is a prototype UI gate, not production authentication.

Development only:

```bash
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm build
node --test tests/*.test.mjs
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest tests -q
```
