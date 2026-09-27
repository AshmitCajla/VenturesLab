from agents.base import AgentSpec, as_json, make_node
from schema import MarketSizing


def build_prompt(state, ctx):
    return f"""You are a quant-focused venture analyst who distrusts inflated TAM slides and demands
bottom-up logic.

Startup: {as_json(state['venture_profile'])}
Market research: {as_json(state.get('market_analysis', {}))}
Pitch-deck claims about market size (verify, do not copy): {ctx.deck or 'n/a'}

Compute, in US dollars as plain numbers:
- TAM: global revenue if 100% share
- SAM: segment reachable with this business model, pricing and initial geography
- SOM: realistic capture in 3-5 years
TAM >= SAM >= SOM must hold. List each step as an assumption with its number
(e.g. "40,000 mid-size clinics in India x $1,200/yr = $48M SAM")."""


SPEC = AgentSpec(
    number=8, name="Market Sizing (TAM/SAM/SOM)", output_key="market_size", schema=MarketSizing,
    role="reasoning", build_prompt=build_prompt,
    rag_query="TAM SAM SOM market size addressable customers pricing",
)
market_sizing_agent = make_node(SPEC)
