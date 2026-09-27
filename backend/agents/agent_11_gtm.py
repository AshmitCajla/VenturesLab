from agents.base import AgentSpec, as_json, make_node
from schema import GTMStrategy


def build_prompt(state, ctx):
    return f"""You are a growth leader who has taken three startups from zero to $10M ARR.

Startup: {as_json(state['venture_profile'])}
Competitors: {as_json(state.get('competitors', []))}
Market sizing: {as_json(state.get('market_size', {}))}
Go-to-market notes from the deck: {ctx.deck or 'n/a'}

Define the ideal customer profile, positioning against the competitors above, a pricing strategy
with actual price points, 2-5 concrete acquisition channels and the sales motion."""


SPEC = AgentSpec(
    number=11, name="Go-To-Market", output_key="gtm_strategy", schema=GTMStrategy,
    role="reasoning", build_prompt=build_prompt,
    rag_query="go to market sales channels pricing customer acquisition partnerships",
)
gtm_strategy_agent = make_node(SPEC)
