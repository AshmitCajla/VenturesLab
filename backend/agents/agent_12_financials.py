from agents.base import AgentSpec, as_json, make_node
from schema import FinancialModel


def build_prompt(state, ctx):
    return f"""You are a startup CFO building a first 5-year financial view.

Startup: {as_json(state['venture_profile'])}
Market sizing: {as_json(state.get('market_size', {}))}
Financial passages from the pitch deck (revenue, burn, pricing, raise):
{ctx.deck or 'none - state that the projections are assumption-based'}

Give CapEx and OpEx line items, the funding required, year-1 and year-5 revenue in US dollars as
plain numbers, and the key assumptions. Year-5 revenue should stay within the obtainable market."""


SPEC = AgentSpec(
    number=12, name="Financial Model", output_key="financial_model", schema=FinancialModel,
    role="reasoning", build_prompt=build_prompt,
    rag_query="financial projections revenue burn rate funding ask pricing unit economics CapEx OpEx",
)
financial_model_agent = make_node(SPEC)
