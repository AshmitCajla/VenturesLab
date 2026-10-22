"""Follow-up questions about a finished run ("why is the score low?").

Answers come only from that run's stored result: agent outputs, checks,
evidence, claims and slide text. The run's content is fenced as untrusted data,
because it was derived from the founder's deck.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from core.llm import call_structured
from services.security import SECURITY_PREAMBLE, fence

CONTEXT_KEYS = ("venture_profile", "scoring", "score", "recommendation", "claims", "market_analysis",
                "market_size", "competitors", "customer_validation", "swot", "porter", "pestle",
                "confidence", "evaluation_summary", "plan", "security", "history", "roadmap",
                "gtm_strategy", "financial_model")


class Answer(BaseModel):
    answer: str = Field(description="direct answer in 2-6 sentences, citing sections and slides")
    sections: list[str] = Field(description="report sections the answer relies on")
    grounded: bool = Field(description="false if the run's data does not contain the answer")


def _context(result: dict) -> str:
    ctx = {k: result[k] for k in CONTEXT_KEYS if k in result}
    pages = result.get("deck_pages") or []
    if pages:
        ctx["deck_slides"] = {f"slide {n}": t[:600] for n, t in enumerate(pages, start=1) if t.strip()}
    return json.dumps(ctx, default=str)[:24000]


async def answer(result: dict, question: str, history: list[dict] | None = None) -> Answer:
    convo = "\n".join(f"{m['role']}: {m['text']}" for m in (history or [])[-6:])
    prompt = f"""{SECURITY_PREAMBLE}You are the VenturesLab analyst who wrote this report. Answer the
analyst's follow-up question using ONLY the run data below. Name the section (and slide numbers where
relevant). If the data does not answer it, say so plainly and set grounded=false.

Run data:
{fence('run data', _context(result))}

Conversation so far:
{convo or '(none)'}

Question: {fence('question', question)}"""
    parsed, _ = await call_structured("reasoning", Answer, prompt, "Follow-up Q&A", attempts=3)
    return parsed
