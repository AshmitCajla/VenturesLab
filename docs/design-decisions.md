# Design decisions

Each entry: the decision, the alternative, and why.

### The score is computed in code, not by the model
The model rates five criteria 1-10 with a reason; `weighted_score` combines them with fixed weights
(20/25/25/10/20). *Alternative:* ask the model for a final score. A model-chosen number is not
reproducible, cannot be explained, and is the first thing an injected instruction targets. With a
fixed formula, a manipulated model can still shift ratings, but every rating is visible with its
reason and the formula cannot be talked out of.

### Defences in four layers, each measured on its own
Hidden-text removal (rendering-based), regex quarantine, a policy-based semantic screen, and
spotlighting (fenced untrusted text + preamble). *Alternative:* one "is this an injection?"
classifier. Measured on our corpus: Llama Prompt Guard 2 (86M and 22M) caught the explicit payload but
scored the paraphrased one 0.0006, the same as benign text. A policy classifier (gpt-oss-safeguard-20b)
caught it but is limited to 3 requests/minute on the free tier, so a batched screen on gpt-oss-20b
with the same written policy is used instead. Layers that need no model (rendering, regex) run first
so the screen sees less text and fails open safely.

### Hidden text is detected by rendering, not by reading colours
Each span's declared colour is compared with the pixels actually rendered under it. *Alternative:*
flag white text. White text on a navy banner is legitimate, and text can be hidden with a covering
shape, near-zero opacity or a matching coloured background; only the rendered page tells the truth.
`tests/test_security.py::test_legitimate_low_contrast_text_is_kept` guards against false positives.

### Boastful claims are allowed through the screen on purpose
The screen removes text that tries to steer the *evaluator*, not text that makes *claims*. Deciding
whether "$50B TAM" is true is the verification agent's job; deleting claims would hide exactly the
inflation investors want flagged.

### Citations are checked in code
The extractor must quote the slide; `citation_found` checks the quoted words (and every number in
them) are on that slide. *Alternative:* trust the model's slide numbers. Invented citations are a
common failure, and a citation nobody checks is decoration.

### Confidence comes from evidence coverage, not from the model
Deck passages retrieved, web sources present, checks passed, fallback model used, claim conflicts.
*Alternative:* ask the model how confident it is. Self-reported confidence is poorly calibrated and
moves with phrasing. These are heuristics too, and the docs say so; `evaluation/consistency.py`
measures actual stability.

### The planner may only drop optional agents
The fast model chooses among SWOT, PESTLE, Porter and verification; rules then force PESTLE for
regulated industries, drop verification without a deck, and drop agents the budget cannot cover.
*Alternative:* a fully model-built graph. The score depends on the core agents, and a planner that
can skip them could be steered into skipping the inconvenient ones.

### One LLM call path, walking the provider chain itself
`core/llm.py` tries Groq then OpenRouter, records which model answered, its tokens and cost.
*Alternative:* LangChain `with_fallbacks`. That hides which model produced an answer, so a fallback
silently changes the analyst and the cost dashboard is wrong. Chaos faults are injected on this path.

### Tools go through MCP with per-agent allow-lists
Research tools are an MCP server reached through an in-process transport; each agent may only call
the tools its spec declares. *Alternative:* call Python functions directly. The allow-list limits
what a manipulated agent could do, and the same servers are usable from Claude Desktop unchanged. The
in-process transport keeps the cost at microseconds instead of a network hop.

### Partial reports instead of failed runs
An agent that fails is recorded; dependents are skipped; the report is delivered with sections
marked unavailable. Completeness has its own SLO (95%), separate from run success (99%), so
degradation is visible instead of hidden inside "success".

### SQLite, one process
Runs, feedback, cache, memory and incidents in one SQLite file; the Slack bot and ops agent in the
API process. *Alternative:* Postgres + Redis + workers. For one instance on a free tier, SQLite in
WAL mode is enough and removes two services; the store interfaces are small enough to swap.

### SLOs computed twice
Prometheus evaluates multi-window burn-rate rules; `services/slo.py` computes the same from the
database. *Alternative:* Prometheus only. Free-tier deploys have no Prometheus, and the ops agent and
weekly digest still need SLO status there.

### Ops agent: fixed evidence pack, then reasoning
For an alert it always collects the same telemetry (SLOs, error signatures, agent health, logs,
failed runs, Prometheus) and pre-classifies error signatures, then the reasoning model writes the
diagnosis. *Alternative:* let the model choose tools freely. On an incident, missing the one tool
that had the answer is worse than reading a few extra; free-form questions do use model-chosen tools.
