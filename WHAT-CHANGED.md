# What changed

## 1. Docstrings → plain comments

`payment_service.py` and `monday_service.py` had `"""triple-quoted"""`
docstrings on the module and each function. Both are rewritten to use
plain `#` comments throughout instead. Behavior is identical — this was
a style-only change, re-verified with a full import and route test
after editing.

One thing intentionally NOT changed: inside `create_monday_record`,
there's still a `"""..."""` block holding the actual GraphQL query
text. That's a real string value the code sends to Monday.com's API,
not documentation — different thing that happens to use the same
quote marks.

`payment.ts`, `monday.ts`, and `scanner_api.py`'s payment/monday routes
never had docstrings to begin with — nothing to change there.

## 2. Monday.com removed from the active app, saved for later

Pulled out entirely rather than left disconnected-but-present, so
there's no unused/dead code sitting in what you present:

- `scanner_api.py` — the Monday import and the `/api/monday/create_record`
  route are gone.
- `App.tsx` — the Monday import and the logging call in
  `handlePaymentAndPrint` are gone.
- `requirements-scanner.txt` — `requests` removed (nothing else needs it).
- `.env.example` — `MONDAY_API_TOKEN`/`MONDAY_BOARD_ID` removed.
- `SCANNER_SETUP.md`, `technical-workflow-notes.md`,
  `demo-explanation.md` — all Monday.com mentions removed, each with a
  one-line pointer to `saved-for-later/monday-integration/` instead.
- `build-technical.cjs` — the payment diagram's subtitle and the
  print-workflow diagram's footer both had Monday.com mentions; both
  removed and the diagrams regenerated (checked visually — no
  overlapping text either).
- `monday_service.py` and `src/monday.ts` themselves — moved into
  `saved-for-later/monday-integration/`, with a step-by-step
  `HOW-TO-RECONNECT.md` in that folder for whenever you want it back.

Re-verified after all of this: ran the actual Flask app (imported with
`monday_service.py` physically absent, not just unreferenced) and
confirmed `/api/monday/create_record` no longer resolves, while every
payment route still works exactly as before. TypeScript still compiles
clean without `monday.ts` in the build.

## Where everything goes

| File here | Goes to |
|---|---|
| `integrated-kiosk/payment_service.py` | `integrated-kiosk/payment_service.py` |
| `integrated-kiosk/scanner_api.py` | `integrated-kiosk/scanner_api.py` |
| `integrated-kiosk/requirements-scanner.txt` | `integrated-kiosk/requirements-scanner.txt` |
| `integrated-kiosk/.env.example` | `integrated-kiosk/.env.example` |
| `integrated-kiosk/SCANNER_SETUP.md` | `integrated-kiosk/SCANNER_SETUP.md` |
| `integrated-kiosk/src/App.tsx` | `integrated-kiosk/src/App.tsx` |
| `integrated-kiosk/src/payment.ts` | `integrated-kiosk/src/payment.ts` |
| `demo-assets/*` | `demo-assets/*` |
| `saved-for-later/` | wherever you want to keep it — it's not part of the live app anymore |

**Also delete from your project** (they're being removed, not replaced):
- `integrated-kiosk/monday_service.py`
- `integrated-kiosk/src/monday.ts`

(Their content lives on in `saved-for-later/monday-integration/` —
don't lose them, just don't leave duplicates in both places.)
