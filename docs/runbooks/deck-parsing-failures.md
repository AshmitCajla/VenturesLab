# Runbook: decks cannot be parsed

**Alerts:** `DeckParsingFailures` (ticket)
**Category:** `deck_parsing`

The run continues on the description and website alone and warns "Pitch deck skipped".

## Check
1. `recent_runs` + run warnings: which files, from which source (Slack, API, MCP)?
2. Is it one sender (corrupt exports, password-protected PDFs) or every deck (a PyMuPDF regression)?
3. Reproduce: `python -c "import pymupdf; pymupdf.open('deck.pdf')"`.

## Mitigate
- Single sender: ask for a re-export ("Save as PDF", not "Print to PDF" with encryption).
- Regression after a dependency bump: pin `pymupdf` to the last good version and redeploy.

## Verify
New decks show "Indexed pitch deck (N pages…)" in progress.
