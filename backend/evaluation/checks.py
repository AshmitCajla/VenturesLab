"""Per-agent evaluation checks.

Pydantic already guarantees *shape* (types, ranges, required fields). These
checks catch outputs that are well-formed but wrong or inconsistent - the
failure modes that actually showed up while building this system:

* market sizes out of order (SOM > SAM) or implausible
* the startup listed as its own competitor, duplicate competitors
* empty or copy-pasted SWOT quadrants
* a "nice to have" problem with a 9/10 pain score
* year-5 revenue smaller than year-1, or revenue > the obtainable market

``error`` checks trigger one automatic self-correction retry of the agent
with the failure message fed back; ``warn`` checks are only reported.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass
class Check:
    agent: str
    name: str
    passed: bool
    severity: Literal["error", "warn"] = "error"
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _nonempty(text: str, min_chars: int = 15) -> bool:
    return isinstance(text, str) and len(text.strip()) >= min_chars


# --------------------------------------------------------------- per agent
def check_profile(d: dict, state: dict) -> list[Check]:
    a = "venture_profile"
    return [
        Check(a, "fields_substantive",
              all(_nonempty(d.get(k, "")) for k in ("problem", "solution", "customer")),
              detail="problem/solution/customer must each be a real sentence"),
        Check(a, "customer_is_specific",
              _norm(d.get("customer", "")) not in {"everyone", "all people", "anyone", "general public"},
              detail="target customer is too broad"),
    ]


def check_market(d: dict, state: dict) -> list[Check]:
    a = "market_analysis"
    return [
        Check(a, "has_growth_drivers", len(d.get("growth_drivers", [])) >= 2,
              detail="need at least two growth drivers"),
        Check(a, "cites_sources", len(d.get("sources", [])) >= 1, severity="warn",
              detail="no web sources cited"),
    ]


def check_competitors(items: list, state: dict) -> list[Check]:
    a = "competitors"
    names = [_norm(c.get("company", "")) for c in items]
    own = _norm(state.get("venture_profile", {}).get("name", ""))
    return [
        Check(a, "count_3_to_7", 3 <= len(items) <= 7, detail=f"got {len(items)} competitors"),
        Check(a, "unique_names", len(set(names)) == len(names), detail="duplicate competitors"),
        Check(a, "not_self", not own or own not in names,
              detail="the venture itself was listed as a competitor"),
    ]


def check_swot(d: dict, state: dict) -> list[Check]:
    a = "swot"
    quads = ("strengths", "weaknesses", "opportunities", "threats")
    all_items = [_norm(x) for q in quads for x in d.get(q, [])]
    return [
        Check(a, "all_quadrants_filled", all(len(d.get(q, [])) >= 2 for q in quads),
              detail="each quadrant needs at least two points"),
        Check(a, "no_repeated_points", len(set(all_items)) == len(all_items),
              detail="the same point appears in more than one quadrant"),
    ]


def check_pestle(d: dict, state: dict) -> list[Check]:
    keys = ("political", "economic", "social", "technological", "legal", "environmental")
    return [Check("pestle", "all_factors_substantive", all(_nonempty(d.get(k, "")) for k in keys),
                  detail="every PESTLE factor needs a real explanation")]


def check_porter(d: dict, state: dict) -> list[Check]:
    scores = [f.get("score") for f in d.values() if isinstance(f, dict)]
    return [
        Check("porter", "five_forces_present", len(scores) == 5),
        Check("porter", "not_all_identical", len(set(scores)) > 1, severity="warn",
              detail="all five forces got the same score"),
    ]


def check_customer(d: dict, state: dict) -> list[Check]:
    nice = d.get("real_pain_or_nice_to_have") == "nice to have"
    pain = d.get("customer_pain_score", 0)
    return [Check("customer_validation", "pain_label_matches_score",
                  not (nice and pain >= 8) and not (not nice and pain <= 3),
                  detail=f"label '{d.get('real_pain_or_nice_to_have')}' contradicts pain score {pain}")]


def check_market_size(d: dict, state: dict) -> list[Check]:
    tam, sam, som = d.get("tam_usd", 0), d.get("sam_usd", 0), d.get("som_usd", 0)
    return [
        Check("market_size", "tam_ge_sam_ge_som", tam >= sam >= som > 0,
              detail=f"TAM={tam:,.0f} SAM={sam:,.0f} SOM={som:,.0f}"),
        Check("market_size", "som_share_plausible", tam == 0 or som / tam <= 0.2, severity="warn",
              detail="SOM is more than 20% of TAM"),
        Check("market_size", "shows_assumptions", len(d.get("assumptions", [])) >= 3,
              detail="needs at least three explicit assumptions"),
    ]


def check_scoring(d: dict, state: dict) -> list[Check]:
    checks = [Check("scoring", "rationale_present", _nonempty(d.get("verdict_rationale", ""), 40))]
    cv = state.get("customer_validation", {})
    pain = cv.get("customer_pain_score")
    sev = d.get("problem_severity", {}).get("score")
    if isinstance(pain, int) and isinstance(sev, int):
        checks.append(Check("scoring", "consistent_with_customer_agent", abs(pain - sev) <= 4,
                            severity="warn",
                            detail=f"problem severity {sev} vs customer pain {pain}"))
    return checks


def check_roadmap(d: dict, state: dict) -> list[Check]:
    return [Check("roadmap", "phases_have_content",
                  bool(d.get("days_30", {}).get("features")) and bool(d.get("months_12", {}).get("kpis")))]


def check_gtm(d: dict, state: dict) -> list[Check]:
    return [Check("gtm_strategy", "has_channels", len(d.get("marketing_channels", [])) >= 2)]


def check_financials(d: dict, state: dict) -> list[Check]:
    y1, y5 = d.get("year_1_revenue_usd", 0), d.get("year_5_revenue_usd", 0)
    som = state.get("market_size", {}).get("som_usd") or 0
    return [
        Check("financial_model", "revenue_grows", y5 >= y1, detail=f"Y1={y1:,.0f} Y5={y5:,.0f}"),
        Check("financial_model", "y5_within_som", not som or y5 <= som * 1.5, severity="warn",
              detail=f"Y5 revenue {y5:,.0f} exceeds obtainable market {som:,.0f}"),
    ]


CHECKS: dict[str, Callable[[object, dict], list[Check]]] = {
    "venture_profile": check_profile,
    "market_analysis": check_market,
    "competitors": check_competitors,
    "swot": check_swot,
    "pestle": check_pestle,
    "porter": check_porter,
    "customer_validation": check_customer,
    "market_size": check_market_size,
    "scoring": check_scoring,
    "roadmap": check_roadmap,
    "gtm_strategy": check_gtm,
    "financial_model": check_financials,
}


def run_checks(key: str, output, state: dict) -> list[Check]:
    fn = CHECKS.get(key)
    if fn is None:
        return []
    try:
        return fn(output, state)
    except Exception as exc:  # a buggy check must never break the pipeline
        return [Check(key, "check_crashed", False, "warn", str(exc))]
