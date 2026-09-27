from agents.base import AgentSpec, as_json, make_node
from schema import CustomerValidation


def build_prompt(state, ctx):
    return f"""You are a Y Combinator-style product-market-fit expert: blunt and immune to founder optimism.

Startup: {as_json(state['venture_profile'])}
Customer evidence from the pitch deck (interviews, pilots, traction):
{ctx.deck or 'none provided'}

1. Is this real pain or nice to have?
2. How often does the customer hit this problem?
3. Is the solution objectively faster, cheaper, easier, and does it have a technology advantage?
4. Give a customer pain score 1-10 (urgency and willingness to pay). The score must agree with
   your pain / nice-to-have label."""


SPEC = AgentSpec(
    number=7, name="Customer Validation", output_key="customer_validation",
    schema=CustomerValidation, role="reasoning", build_prompt=build_prompt,
    rag_query="customer interviews pilots traction users revenue retention testimonials",
)
customer_validation_agent = make_node(SPEC)
