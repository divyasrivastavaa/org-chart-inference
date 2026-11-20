"""
db/costs.py  -  Phase 2: cost tracking. The ONE place for prices.

cost_usd in the runs table = Gemini tokens x price per token + PDL credits x price per credit.
The rules cost nothing. Prices are list prices, so a run shows what it would cost on a
paid plan, even while we use free tiers.
"""

# Gemini API, standard paid tier, USD per 1 million tokens (output includes thinking tokens).
# Source: https://ai.google.dev/gemini-api/docs/pricing  (checked 20 Nov 2026)
# The free tier costs $0; these are the paid list prices.
GEMINI_PRICE_PER_MILLION = {
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
}
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"   # judge_gemini.py's default

# People Data Labs: we use the free plan (about 100 credits a month), so we pay $0 per
# credit. Credits are still counted in runs.pdl_credits_used. Change this if we ever pay.
PDL_PRICE_PER_CREDIT = 0.0


def gemini_cost(input_tokens, output_tokens, model=DEFAULT_GEMINI_MODEL):
    price = GEMINI_PRICE_PER_MILLION[model]
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def run_cost(gemini_input_tokens=0, gemini_output_tokens=0, pdl_credits=0,
             model=DEFAULT_GEMINI_MODEL):
    """Cost of one run in USD (rules only: 0)."""
    return round(gemini_cost(gemini_input_tokens, gemini_output_tokens, model)
                 + pdl_credits * PDL_PRICE_PER_CREDIT, 6)
