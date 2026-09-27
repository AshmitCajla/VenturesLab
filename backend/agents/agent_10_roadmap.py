from agents.base import AgentSpec, as_json, make_node
from schema import Roadmap


def build_prompt(state, ctx):
    return f"""You are a serial technical founder and fractional CTO who cuts scope to ship and validate fast.

Startup: {as_json(state['venture_profile'])}
Customer validation: {as_json(state.get('customer_validation', {}))}
Investor concerns: {as_json(state.get('scoring', {}).get('verdict_rationale', ''))}

Lean roadmap:
- 30 days: cheapest way to prove willingness to pay; the few features that matter
- 90 days: MVP architecture and tech requirements for the first 100 paying users
- 12 months: product-market-fit goal, measurable KPIs and the roles to hire"""


SPEC = AgentSpec(
    number=10, name="MVP Roadmap", output_key="roadmap", schema=Roadmap, role="reasoning",
    build_prompt=build_prompt, rag_query="product roadmap features technology milestones",
)
mvp_roadmap_agent = make_node(SPEC)
