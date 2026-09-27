from agents.base import AgentSpec, as_json, make_node
from schema import PESTLEAnalysis


def build_prompt(state, ctx):
    return f"""You are a macro-economist and regulatory strategist. Assess the external environment
this venture operates in.

Startup: {as_json(state['venture_profile'])}
Market: {as_json(state.get('market_analysis', {}))}

For each PESTLE factor write 2-3 sentences naming concrete regulations, trends or risks
relevant to this specific industry and geography (not generic statements)."""


SPEC = AgentSpec(
    number=5, name="PESTLE", output_key="pestle", schema=PESTLEAnalysis, role="reasoning",
    build_prompt=build_prompt,
)
pestle_agent = make_node(SPEC)
