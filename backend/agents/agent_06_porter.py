from agents.base import AgentSpec, as_json, make_node
from schema import PorterAnalysis


def build_prompt(state, ctx):
    return f"""You are an industrial-organisation economist. Apply Porter's Five Forces to the industry
this startup is entering.

Startup: {as_json(state['venture_profile'])}
Competitors: {as_json(state.get('competitors', []))}
SWOT: {as_json(state.get('swot', {}))}

Score each force 1-10 (1 = weak force, favourable for the startup; 10 = strong force) and give a
one-to-two sentence reason grounded in the competitors and market above. Scores should differ
where the forces genuinely differ."""


SPEC = AgentSpec(
    number=6, name="Porter's Five Forces", output_key="porter", schema=PorterAnalysis,
    role="reasoning", build_prompt=build_prompt,
)
porter_agent = make_node(SPEC)
