from agents.base import AgentSpec, make_node
from schema import VentureProfile


def build_prompt(state, ctx):
    context = ""
    if state.get("idea"):
        context += f"Founder's description:\n{state['idea']}\n\n"
    if state.get("url_content"):
        context += f"Website content:\n{state['url_content'][:6000]}\n\n"
    if state.get("deck_summary"):
        context += f"Pitch deck (opening slides):\n{state['deck_summary']}\n\n"
    if ctx.deck:
        context += f"Pitch deck (most relevant passages):\n{ctx.deck}\n\n"
    return f"""You are a Principal Startup Analyst at a Tier-1 venture capital firm, known for turning
ambiguous pitches into precise business mechanics.

Context:
{context}
Your directive:
1. Identify the *actual* problem, stripping away marketing language. Is it a painkiller or a vitamin?
2. Define the solution by its mechanism and how it creates value.
3. Pinpoint the exact target customer segment (never "everyone").
4. Classify the business model precisely (B2B SaaS, marketplace, D2C, hardware-as-a-service, ...).
5. Assess the stage from the maturity of the evidence (ideation, pre-seed MVP, seed traction, ...).

Use only the context above. Where it is silent, say so instead of inventing facts."""


SPEC = AgentSpec(
    number=1, name="Venture Understanding", output_key="venture_profile",
    schema=VentureProfile, role="fast", build_prompt=build_prompt, requires=(),
    rag_query="problem solution product customer business model traction team",
)
venture_understanding_agent = make_node(SPEC)
