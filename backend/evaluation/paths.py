"""Where evaluation inputs and outputs live."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"  # inputs: labelled cases, golden decks, baseline, benign corpus
GOLDEN = DATA / "golden"
REPORTS = ROOT / "reports"  # outputs committed as evidence: red team, chaos, golden, consistency
