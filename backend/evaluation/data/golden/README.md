# Golden set

Hand-labelled decks make the evaluation numbers trustworthy. To add one:

1. Put the PDF here as `<id>.pdf` (public decks only; do not commit confidential material).
2. Add an entry to `labels.json`:

```json
{
  "id": "airbnb-2008",
  "expected": ["BUILD", "PIVOT"],
  "idea": "",
  "key_numbers": {"market_size": ["$1.9B", 6], "users": ["630,000", 7]},
  "red_flags": [],
  "labelled_by": "your name",
  "notes": "why this verdict range is right"
}
```

* `expected`: the verdicts a careful human reviewer would accept.
* `key_numbers`: `kind: [value as written, slide number]`; kinds are those in `schema.ClaimKind`.
* `red_flags`: `inflated_market_claim` (the market claim should be flagged as a conflict) and/or `hidden_text`.

Then run `python -m evaluation.golden` (live) and commit the report. Aim for 20-30 decks before
trusting verdict accuracy; with fewer, report the count next to every number.

Analyst corrections from Slack (`✏️ Correct a section`) are exported with
`python -m evaluation.judge --export-corrections` and become regression cases for the judge.
