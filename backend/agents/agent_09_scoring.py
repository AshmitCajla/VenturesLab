"""Scoring agent: the LLM judges five criteria, the final score is computed in
code with fixed weights, so the BUILD / PIVOT / PASS decision is deterministic
and explainable instead of a number the model makes up."""
from agents.base import AgentSpec, as_json, make_node
from core.config import get_settings
from schema import SCORING_WEIGHTS, ScoringResult


def weighted_score(result: dict) -> float:
    total = sum(w * (result[k]["score"] - 1) / 9 for k, w in SCORING_WEIGHTS.items())
    return round(total, 3)


def recommendation_for(score: float) -> str:
    threshold = get_settings().build_threshold
    if score >= threshold:
        return "BUILD"
    if score >= threshold - 0.2:
        return "PIVOT"
    return "PASS"


def build_prompt(state, ctx):
    evidence = {k: state.get(k, {}) for k in (
        "venture_profile", "market_analysis", "market_size", "competitors",
        "swot", "porter", "customer_validation")}
    return f"""You are the managing partner of a $500M venture fund that backs only category-defining
companies. Judge this venture on the evidence gathered by your analysts.

Evidence: {as_json(evidence)}

Score each criterion 1-10 with a one-sentence reason:
- problem_severity: will customers go out of their way to solve this?
- market_size: is the SAM large enough to support a $1B+ company?
- differentiation: is there an unfair, defensible advantage?
- competition: 10 = fragmented / weak incumbents, 1 = entrenched monopoly
- business_model: are the unit economics viable?
Then give your confidence and a short verdict rationale. Be candid; do not be polite."""


def postprocess(parsed, state):
    data = parsed.model_dump()
    score = weighted_score(data)
    data.update(weighted_score=score, weights=SCORING_WEIGHTS)
    return {"scoring": data, "score": score, "recommendation": recommendation_for(score)}


SPEC = AgentSpec(
    number=9, name="Venture Scoring", output_key="scoring", schema=ScoringResult,
    role="reasoning", build_prompt=build_prompt, postprocess=postprocess,
    requires=("venture_profile", "customer_validation"),
)
venture_scoring_agent = make_node(SPEC)
