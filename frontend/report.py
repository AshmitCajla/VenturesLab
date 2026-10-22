"""PDF export of a finished analysis (ReportLab)."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def usd(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:,.1f}{unit}"
    return f"${x:,.0f}"


# The built-in PDF fonts only cover Latin-1; models write typographic characters (non-breaking
# hyphens, narrow spaces, curly quotes) that would otherwise render as wrong glyphs.
_PLAIN = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2248": "~",
    }
)


def plain(text) -> str:
    return str(text).translate(_PLAIN).encode("latin-1", "replace").decode("latin-1")


def ok(section) -> bool:
    return isinstance(section, dict) and section and "error" not in section and "skipped" not in section


def build_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, textColor=colors.HexColor("#0F172A"))
    h2 = ParagraphStyle(
        "h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor("#1D4ED8"), spaceBefore=12, spaceAfter=4
    )
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=9, leading=13)
    P = lambda t, s=body: Paragraph(escape(plain(t)), s)  # noqa: E731
    story = []

    profile = data.get("venture_profile", {})
    story += [
        P(f"VenturesLab report: {profile.get('name', 'Venture')}", h1),
        HRFlowable(width="100%", color=colors.HexColor("#E2E8F0")),
        Spacer(1, 6),
        P(f"Score {data.get('score', 0):.0%}  |  Recommendation: {data.get('recommendation', 'n/a')}", h2),
    ]
    scoring = data.get("scoring", {})
    if ok(scoring):
        story.append(P(scoring.get("verdict_rationale", "")))

    claims = (data.get("claims") or {}).get("claims", [])
    if claims:
        story.append(P("Deck claims checked", h2))
        rows = [["Claim", "Slide", "Citation", "Check", "Independent estimate"]] + [
            [
                P(c["claim"]),
                str(c["slide"]),
                c["citation"].replace("_", " "),
                plain(c["status"]),
                P(c.get("external_estimate") or ""),
            ]
            for c in claims
        ]
        t = Table(rows, colWidths=[200, 35, 75, 70, 135])
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(t)
    sec = data.get("security") or {}
    if sec.get("hidden_text") or sec.get("injections"):
        story += [
            P("Manipulation removed from the inputs", h2),
            P(
                f"{sec.get('hidden_text', 0)} hidden passage(s) and {sec.get('injections', 0)} instruction(s) "
                "aimed at the AI were removed before analysis:"
            ),
        ]
        story += [P(f'- {f["location"]}: {f["reason"]}: "{f["excerpt"][:160]}"') for f in sec.get("findings", [])[:8]]
    conf = data.get("confidence") or {}
    if conf:
        story += [
            P("Confidence by section", h2),
            P("; ".join(f"{k.replace('_', ' ')}: {v['level']}" for k, v in conf.items())),
        ]

    if ok(profile):
        story.append(P("1. Venture overview", h2))
        for k in ("problem", "solution", "customer", "business_model", "stage"):
            story.append(P(f"{k.replace('_', ' ').title()}: {profile.get(k, '')}"))

    ms = data.get("market_size", {})
    if ok(ms):
        story += [
            P("2. Market sizing", h2),
            P(f"TAM {usd(ms.get('tam_usd'))} | SAM {usd(ms.get('sam_usd'))} | SOM {usd(ms.get('som_usd'))}"),
        ]
        story += [P(f"- {a}") for a in ms.get("assumptions", [])]

    comps = data.get("competitors", [])
    if isinstance(comps, list) and comps and "error" not in comps[0]:
        story.append(P("3. Competitors", h2))
        rows = [["Company", "Product", "Strengths", "Weaknesses"]] + [
            [P(c.get(k, "")) for k in ("company", "product", "strengths", "weaknesses")] for c in comps
        ]
        t = Table(rows, colWidths=[80, 130, 150, 150])
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(t)

    swot = data.get("swot", {})
    if ok(swot):
        story.append(P("4. SWOT", h2))
        for q in ("strengths", "weaknesses", "opportunities", "threats"):
            story.append(P(f"{q.title()}: " + "; ".join(swot.get(q, []))))

    if data.get("recommendation") == "BUILD":
        rd, gtm, fin = data.get("roadmap", {}), data.get("gtm_strategy", {}), data.get("financial_model", {})
        story.append(P("5. Execution plan", h2))
        if ok(rd):
            story += [
                P(f"30 days: {rd['days_30']['validation_plan']}"),
                P(f"90 days: {rd['days_90']['mvp_development']}"),
                P(f"12 months: {rd['months_12']['pmf_goal']}"),
            ]
        if ok(gtm):
            story += [P(f"ICP: {gtm['ideal_customer_profile']}"), P(f"Pricing: {gtm['pricing_strategy']}")]
        if ok(fin):
            story.append(
                P(
                    f"Funding required {usd(fin['funding_required_usd'])} | "
                    f"Y1 revenue {usd(fin['year_1_revenue_usd'])} | Y5 revenue {usd(fin['year_5_revenue_usd'])}"
                )
            )

    summary = data.get("evaluation_summary", {})
    if summary.get("total"):
        story += [P("Quality checks", h2), P(f"{summary['passed']}/{summary['total']} automated checks passed.")]

    doc.build(story)
    return buf.getvalue()
