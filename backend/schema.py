"""Structured outputs every agent must return (enforced via tool-calling)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VentureProfile(BaseModel):
    name: str = Field(description="Venture name, or a short descriptive name if none is given")
    problem: str = Field(description="The core problem the startup solves")
    solution: str = Field(description="The product or service and how it works")
    customer: str = Field(description="Specific target customer segment")
    business_model: str = Field(description="How they make money, e.g. B2B SaaS, marketplace")
    stage: str = Field(description="Ideation, Pre-seed MVP, Seed traction, ...")
    painkiller_or_vitamin: Literal["painkiller", "vitamin"]


class MarketAnalysis(BaseModel):
    industry: str
    market_size_estimate: str = Field(description="Headline market size with year, e.g. '$12B (2025)'")
    cagr: str = Field(description="Compound annual growth rate, e.g. '14%'")
    growth_drivers: list[str]
    market_attractiveness_score: float = Field(ge=1.0, le=10.0)
    sources: list[str] = Field(default_factory=list, description="URLs from the search results used")


class Competitor(BaseModel):
    company: str
    product: str
    pricing: str
    funding: str
    strengths: str
    weaknesses: str


class CompetitorList(BaseModel):
    competitors: list[Competitor] = Field(min_length=1)


class SWOTAnalysis(BaseModel):
    strengths: list[str]
    weaknesses: list[str]
    opportunities: list[str]
    threats: list[str]


class PESTLEAnalysis(BaseModel):
    political: str
    economic: str
    social: str
    technological: str
    legal: str
    environmental: str


class ForceScore(BaseModel):
    score: int = Field(ge=1, le=10, description="1 = weak force (good for startup), 10 = strong")
    reason: str


class PorterAnalysis(BaseModel):
    supplier_power: ForceScore
    buyer_power: ForceScore
    competitive_rivalry: ForceScore
    threat_of_substitutes: ForceScore
    threat_of_new_entrants: ForceScore


class CustomerValidation(BaseModel):
    real_pain_or_nice_to_have: Literal["real pain", "nice to have"]
    frequency_of_problem: str
    target_customer: str
    solution_is_faster: bool
    solution_is_cheaper: bool
    solution_is_easier: bool
    technological_advantage: bool
    customer_pain_score: int = Field(ge=1, le=10)


class MarketSizing(BaseModel):
    tam_usd: float = Field(ge=0, description="Total addressable market in US dollars (number)")
    sam_usd: float = Field(ge=0, description="Serviceable available market in US dollars (number)")
    som_usd: float = Field(ge=0, description="Serviceable obtainable market in US dollars (number)")
    assumptions: list[str] = Field(description="Step-by-step bottom-up assumptions and formulas")


class CriterionScore(BaseModel):
    score: int = Field(ge=1, le=10)
    reason: str


class ScoringResult(BaseModel):
    """The LLM scores each criterion; the final weighted score is computed in code."""

    problem_severity: CriterionScore
    market_size: CriterionScore
    differentiation: CriterionScore
    competition: CriterionScore = Field(description="10 = fragmented/weak competition, 1 = entrenched monopoly")
    business_model: CriterionScore
    confidence: Literal["High", "Medium", "Low"]
    verdict_rationale: str


class Phase30(BaseModel):
    validation_plan: str
    features: list[str]


class Phase90(BaseModel):
    mvp_development: str
    tech_requirements: list[str]


class Phase12M(BaseModel):
    pmf_goal: str
    kpis: list[str]
    team_requirements: list[str]


class Roadmap(BaseModel):
    days_30: Phase30
    days_90: Phase90
    months_12: Phase12M


class GTMStrategy(BaseModel):
    ideal_customer_profile: str
    positioning: str
    pricing_strategy: str
    marketing_channels: list[str]
    sales_strategy: str


class FinancialModel(BaseModel):
    capex: list[str]
    opex: list[str]
    funding_required_usd: float = Field(ge=0)
    year_1_revenue_usd: float = Field(ge=0)
    year_5_revenue_usd: float = Field(ge=0)
    key_assumptions: list[str]


# Weights used by the scoring agent (sum to 1.0)
SCORING_WEIGHTS = {
    "problem_severity": 0.20,
    "market_size": 0.25,
    "differentiation": 0.25,
    "competition": 0.10,
    "business_model": 0.20,
}
