"""Deterministic stand-ins for the LLM so the whole graph runs offline in CI."""
from __future__ import annotations

import copy

import schema as S
from ops.agent import Diagnosis, OpsAnswer, Postmortem, ToolPlan
from services.qa import Answer
from services.security import ScreenResult
from services.vision import SlideVision
from workflow.planner import Plan

GOOD = {
    S.VentureProfile: dict(
        name="ClinicFlow", problem="Small clinics lose 15% of appointments to no-shows and manual scheduling.",
        solution="WhatsApp-first scheduling assistant that confirms, reminds and refills cancelled slots.",
        customer="Independent dental and physiotherapy clinics in Indian tier-1 cities",
        business_model="B2B SaaS, per-clinic monthly subscription", stage="Pre-seed MVP",
        painkiller_or_vitamin="painkiller"),
    S.MarketAnalysis: dict(
        industry="Healthcare practice-management software", market_size_estimate="$1.2B (2025)",
        cagr="11%", growth_drivers=["Clinic digitisation", "WhatsApp Business API adoption"],
        market_attractiveness_score=7.0, sources=["https://example.com/report"]),
    S.CompetitorList: dict(competitors=[
        dict(company=n, product="Clinic scheduling suite", pricing="$30/month", funding="Series A",
             strengths="Brand and integrations", weaknesses="No WhatsApp automation")
        for n in ("Practo Ray", "Zenoti", "Clinicea")]),
    S.SWOTAnalysis: dict(strengths=["WhatsApp native", "Fast onboarding"],
                         weaknesses=["Small team", "No EHR integration"],
                         opportunities=["Tier-2 expansion", "Pharmacy refills"],
                         threats=["Meta API pricing changes", "Incumbent bundling"]),
    S.PESTLEAnalysis: {k: f"{k.title()} factors are relevant for clinic software in India." for k in
                       ("political", "economic", "social", "technological", "legal", "environmental")},
    S.PorterAnalysis: {k: dict(score=v, reason="Reasoned from the competitor set.") for k, v in
                       zip(("supplier_power", "buyer_power", "competitive_rivalry",
                            "threat_of_substitutes", "threat_of_new_entrants"), (3, 6, 7, 5, 6), strict=True)},
    S.CustomerValidation: dict(real_pain_or_nice_to_have="real pain", frequency_of_problem="Daily",
                               target_customer="Clinic front-desk managers", solution_is_faster=True,
                               solution_is_cheaper=True, solution_is_easier=True,
                               technological_advantage=False, customer_pain_score=8),
    S.MarketSizing: dict(tam_usd=1.2e9, sam_usd=9.0e7, som_usd=4.5e6,
                         assumptions=["60k clinics", "x $1,500/yr", "5% capture in 4 years"]),
    S.ScoringResult: dict(**{k: dict(score=8, reason="Strong evidence.") for k in
                             ("problem_severity", "market_size", "differentiation", "competition",
                              "business_model")},
                          confidence="Medium",
                          verdict_rationale="Clear, frequent pain with a reachable SAM and a cheap channel."),
    S.Roadmap: dict(days_30=dict(validation_plan="Concierge pilot with 10 clinics", features=["Reminders"]),
                    days_90=dict(mvp_development="FastAPI + WhatsApp API", tech_requirements=["Postgres"]),
                    months_12=dict(pmf_goal="200 paying clinics", kpis=["No-show rate"],
                                   team_requirements=["Sales lead"])),
    S.GTMStrategy: dict(ideal_customer_profile="3-10 chair dental clinics", positioning="Fill every slot",
                        pricing_strategy="$25/month", marketing_channels=["Dental associations", "Referrals"],
                        sales_strategy="Founder-led field sales"),
    S.FinancialModel: dict(capex=["Laptops"], opex=["Cloud", "Salaries"], funding_required_usd=5e5,
                           year_1_revenue_usd=6e4, year_5_revenue_usd=3e6,
                           key_assumptions=["$25 ARPU", "8% monthly growth"]),
    S.ClaimList: dict(claims=[
        dict(claim="The company has $2M in revenue", kind="revenue", value="$2M", slide=3,
             quote="revenue $2M", search_query="clinicflow revenue"),
        dict(claim="Clinic software is a $50B market", kind="market_size", value="$50B", slide=2,
             quote="a $50B market", search_query="clinic scheduling software market size")]),
    S.VerdictList: dict(verdicts=[dict(index=1, status="conflict", external_estimate="$6-9B (2025)",
                                       sources=["https://example.com/report"],
                                       note="Independent reports are far smaller.")]),
    Plan: dict(choices=[dict(agent=a, run=True, reason="useful here") for a in ("swot", "pestle", "porter", "claims")]),
    ScreenResult: dict(violations=[]),
    SlideVision: dict(text_in_images="", figures=[], chart_summary=""),
    Diagnosis: dict(summary="Agents are failing on LLM timeouts.", likely_cause="Primary provider timing out",
                    category="llm_timeout", evidence=["6x llm_timeout signature in 1h"], confidence="Medium",
                    next_steps=["Check provider status", "Lower LLM_TIMEOUT_S"]),
    ToolPlan: dict(calls=[dict(tool="error_signatures", arguments={"hours": 16}),
                          dict(tool="not_a_tool", arguments={})]),
    OpsAnswer: dict(answer="Two runs failed overnight on rate limits.", tools_used=[]),
    Postmortem: dict(markdown="## Summary\nLLM timeouts degraded reports."),
    Answer: dict(answer="The score is high because the pain is frequent (customer validation).",
                 sections=["scoring", "customer_validation"], grounded=True),
}


class FakeStructured:
    def __init__(self, llm: FakeLLM, schema):
        self.llm, self.schema = llm, schema

    async def ainvoke(self, prompt):
        self.llm.prompts.append((self.schema.__name__, prompt))
        queue = self.llm.scripted.get(self.schema)
        payload = queue.pop(0) if queue else GOOD[self.schema]
        if isinstance(payload, Exception):
            raise payload
        return self.schema.model_validate(copy.deepcopy(payload))


class FakeLLM:
    """Returns GOOD outputs unless a schema has scripted responses queued."""

    def __init__(self, scripted: dict | None = None):
        self.scripted = {k: list(v) for k, v in (scripted or {}).items()}
        self.prompts: list[tuple[str, str]] = []

    def with_structured_output(self, schema, **kwargs):
        return FakeStructured(self, schema)
