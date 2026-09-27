from agents.base import AgentSpec, as_json, make_node
from schema import SWOTAnalysis


def build_prompt(state, ctx):
    return f"""You are a McKinsey Senior Partner. Produce a SWOT analysis that is specific to this venture,
not generic startup advice.

Startup: {as_json(state['venture_profile'])}
Market: {as_json(state.get('market_analysis', {}))}
Competitors: {as_json(state.get('competitors', []))}

3-5 points per quadrant. Strengths/weaknesses are internal; opportunities/threats are external.
Never repeat the same point in two quadrants."""


SPEC = AgentSpec(
    number=4, name="SWOT", output_key="swot", schema=SWOTAnalysis, role="reasoning",
    build_prompt=build_prompt,
)
swot_agent = make_node(SPEC)
