# Chaos experiment report

Mode: fake models (plumbing and deterministic signals only)

| experiment | fault | runs | delivered | failed | sections unavailable (avg) | degraded inputs | SLO alerts fired | top error signals | injected cause among them |
|---|---|---|---|---|---|---|---|---|---|
| baseline | — | 2 | 2 | 0 | 0.0 | 0 | — | unknown | ✓ |
| llm_timeouts | llm_timeout | 2 | 2 | 0 | 1.0 | 0 | LLMErrorRateHigh, ReportCompletenessBudgetBurn | llm_timeout | ✓ |
| primary_provider_down | provider_down | 2 | 2 | 0 | 1.0 | 0 | ReportCompletenessBudgetBurn | llm_provider_outage | ✓ |
| web_search_down | search_down | 2 | 2 | 0 | 0.0 | 4 | WebSearchDegraded | web_search | ✓ |
| malformed_decks | deck_corrupt | 2 | 2 | 0 | 0.0 | 2 | DeckParsingFailures | deck_parsing | ✓ |
| request_burst | — | 8 | 8 | 0 | 0.0 | 0 | — | unknown | ✓ |
