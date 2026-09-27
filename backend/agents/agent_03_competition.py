from agents.base import AgentSpec, as_json, make_node
from schema import CompetitorList


def web_query(state):
    p = state["venture_profile"]
    return f"top competitors alternatives to {p['solution']} for {p['customer']}"[:200]


def build_prompt(state, ctx):
    return f"""You are a Lead Competitive Intelligence Strategist. You look beyond direct competitors to
substitutes, internal workarounds and the status quo the startup must beat.

Startup: {as_json(state['venture_profile'])}
Web search results:
{ctx.web}
What the pitch deck says about competition:
{ctx.deck or 'n/a'}

Identify 3 to 5 real competitors or alternatives (never the startup itself, no duplicates).
For each: core product, pricing (estimate if unknown and say so), funding, structural
strengths (network effects, switching costs ...) and weaknesses this venture could exploit."""


def postprocess(parsed, state):
    return {"competitors": [c.model_dump() for c in parsed.competitors]}


SPEC = AgentSpec(
    number=3, name="Competition", output_key="competitors", schema=CompetitorList,
    role="fast", build_prompt=build_prompt, web_query=web_query, web_results=7,
    rag_query="competitors alternatives competitive advantage differentiation",
    postprocess=postprocess,
)
competition_agent = make_node(SPEC)
