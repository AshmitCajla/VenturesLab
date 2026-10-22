# Runbook: manipulated decks detected

**Signal:** `ventureslab_injection_findings_total` rising; reports carry a "hidden passages removed" red flag.

Not an outage: the defences worked. Track it because a spike can mean a coordinated attempt, or a
false-positive regression in the detectors.

## Check
1. The report's `security.findings`: what was removed, from which slide, and why.
2. False positives? If ordinary pitch copy is being removed, add it to the benign set in
   `tests/test_security.py` and adjust `services/security.py`.

## Mitigate
- Real attempts: tell the deal team; the deck's other claims deserve extra diligence.
- False positives: fix the pattern, add a regression test, rerun `python -m evaluation.redteam`.
