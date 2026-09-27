from agents.base import AgentSpec, as_json, make_node
from schema import MarketAnalysis


def web_query(state):
    p = state["venture_profile"]
    return f"{p['solution']} {p['customer']} market size CAGR industry report"[:200]


def build_prompt(state, ctx):
    return f"""You are the Director of Market Intelligence at a private-equity firm. You triangulate
conflicting web data into defensible market estimates.

Startup profile: {as_json(state['venture_profile'])}
Web search results:
{ctx.web}
Pitch deck passages about the market:
{ctx.deck or 'n/a'}

Directive:
1. Name the industry and give a headline market size with its year, and the CAGR.
2. List the structural growth drivers.
3. Score market attractiveness 1-10 (fragmentation, growth tailwinds, CAGR stability).
4. In `sources`, list only URLs that actually appear in the search results above.
Be objective; do not inflate numbers."""


SPEC = AgentSpec(
    number=2, name="Market Research", output_key="market_analysis", schema=MarketAnalysis,
    role="fast", build_prompt=build_prompt, web_query=web_query,
    rag_query="market size TAM industry growth opportunity",
)
market_research_agent = make_node(SPEC)
