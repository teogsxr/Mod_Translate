"""LLM API pricing registry — accurate as of March 2026.

All prices are USD per 1,000,000 tokens (input, output).
Use calculate_cost() for all cost estimates across the app.

Sources verified March 2026:
  - OpenAI:    platform.openai.com/docs/pricing
  - Anthropic: anthropic.com/pricing
  - Google:    ai.google.dev/pricing
  - DeepSeek:  api-docs.deepseek.com/quick_start/pricing
  - Mistral:   mistral.ai/technology/pricing/
  - Cohere:    cohere.com/pricing

Matching strategy (applied in order):
  1. Exact match on lowercase model ID
  2. Prefix match — handles versioned IDs like
     "claude-sonnet-4-6-20250514" → "claude-sonnet-4-6"
  3. Substring match as last resort
  4. Returns 0.0 if no match (free / local / unknown model)
"""

from typing import Optional

# (input_per_1M_USD, output_per_1M_USD)
_PricingRow = tuple[float, float]

# ──────────────────────────────────────────────────────────────────────────────
# PRICING TABLES  (keys are lowercase, without date suffixes)
# ──────────────────────────────────────────────────────────────────────────────

_OPENAI: dict[str, _PricingRow] = {
    # Flagship
    "gpt-4o":                 (2.50,  10.00),
    "gpt-4o-mini":            (0.15,   0.60),
    # GPT-4 legacy
    "gpt-4-turbo":            (5.00,  15.00),
    "gpt-4":                  (30.00, 60.00),
    "gpt-3.5-turbo":          (0.50,   1.50),
    # Reasoning o-series
    "o1":                     (15.00, 60.00),
    "o1-mini":                (0.55,   2.20),
    "o1-pro":                 (150.00, 600.00),
    "o3":                     (2.00,   8.00),
    "o3-mini":                (1.10,   4.40),
    "o4-mini":                (1.10,   4.40),
    # GPT-4.1 (2025)
    "gpt-4.1":                (2.00,   8.00),
    "gpt-4.1-mini":           (0.40,   1.60),
    "gpt-4.1-nano":           (0.10,   0.40),
}

_ANTHROPIC: dict[str, _PricingRow] = {
    # Claude 4.6 (latest as of 2026-03)
    "claude-opus-4-6":        (5.00,  25.00),
    "claude-sonnet-4-6":      (3.00,  15.00),
    # Claude 4.5
    "claude-opus-4-5":        (5.00,  25.00),
    "claude-sonnet-4-5":      (3.00,  15.00),
    "claude-haiku-4-5":       (1.00,   5.00),
    # Claude 4 (base generation)
    "claude-opus-4":          (15.00, 75.00),
    "claude-sonnet-4":        (3.00,  15.00),
    # Claude 3.5
    "claude-3-5-sonnet":      (3.00,  15.00),
    "claude-3-5-haiku":       (0.80,   4.00),
    # Claude 3
    "claude-3-opus":          (15.00, 75.00),
    "claude-3-sonnet":        (3.00,  15.00),
    "claude-3-haiku":         (0.25,   1.25),
}

_GEMINI: dict[str, _PricingRow] = {
    # Gemini 2.5  (≤200K context tier; larger context is ~2x input)
    "gemini-2.5-pro":         (1.25,  10.00),
    "gemini-2.5-flash":       (0.30,   2.50),
    # Gemini 2.0
    "gemini-2.0-flash":       (0.10,   0.40),
    "gemini-2.0-pro":         (0.00,   0.00),   # experimental / deprecated
    # Gemini 1.5
    "gemini-1.5-pro":         (1.25,   5.00),
    "gemini-1.5-flash":       (0.35,   1.05),
    "gemini-1.5-flash-8b":    (0.075,  0.30),
    # Gemini 1.0
    "gemini-1.0-pro":         (0.125,  0.375),
}

_DEEPSEEK: dict[str, _PricingRow] = {
    # DeepSeek V3.2  (cache-miss pricing — most conservative / accurate for
    # new requests; cache-hit is 10× cheaper but can't be predicted)
    "deepseek-chat":          (0.28,   0.42),
    "deepseek-v3":            (0.28,   0.42),
    # DeepSeek R1 / Reasoner
    "deepseek-reasoner":      (0.55,   2.19),
    "deepseek-r1":            (0.55,   2.19),
}

_MISTRAL: dict[str, _PricingRow] = {
    # Mistral Large (latest)
    "mistral-large":          (3.00,   9.00),
    "mistral-medium":         (2.70,   8.10),
    "mistral-small":          (1.00,   3.00),
    "mistral-saba":           (0.20,   0.60),
    # Open / free-tier models
    "open-mistral-7b":        (0.25,   0.25),
    "open-mixtral-8x7b":      (0.70,   0.70),
    "open-mixtral-8x22b":     (2.00,   6.00),
    # Codestral
    "codestral":              (0.30,   0.90),
}

_COHERE: dict[str, _PricingRow] = {
    # Command A (flagship 2025)
    "command-a":              (2.50,  10.00),
    # Command R+
    "command-r-plus":         (2.50,  10.00),
    "command-r+":             (2.50,  10.00),
    # Command R
    "command-r":              (0.15,   0.60),
    # Command (legacy)
    "command":                (1.00,   2.00),
    "command-light":          (0.30,   0.60),
}

# Providers with no usage-based cost (local / self-hosted)
_FREE_PROVIDERS = {"ollama", "vllm", "custom", "lmstudio"}

# Master registry: provider_id → pricing table
_REGISTRY: dict[str, dict[str, _PricingRow]] = {
    "openai":     _OPENAI,
    "anthropic":  _ANTHROPIC,
    "gemini":     _GEMINI,
    "deepseek":   _DEEPSEEK,
    "mistral":    _MISTRAL,
    "cohere":     _COHERE,
    "deepl":      {
        "deepl":      (25.00, 0.00),   # $25/1M chars input, output is the translation
        "deepl-next": (25.00, 0.00),
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def get_pricing(provider_id: str, model_id: str) -> Optional[_PricingRow]:
    """Return (input_per_1M, output_per_1M) or None if unknown/free."""
    if provider_id in _FREE_PROVIDERS:
        return (0.0, 0.0)

    table = _REGISTRY.get(provider_id)
    if not table:
        return None

    key = model_id.lower().strip()

    # 1. Exact match
    if key in table:
        return table[key]

    # 2. Prefix match — e.g. "claude-sonnet-4-6-20250514" → "claude-sonnet-4-6"
    for model_key, prices in table.items():
        if key.startswith(model_key):
            return prices

    # 3. Substring match — e.g. model IDs that embed the key somewhere
    for model_key, prices in table.items():
        if model_key in key:
            return prices

    return None


def calculate_cost(
    provider_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Return estimated USD cost for a single API call.

    Returns 0.0 for local/free providers or unrecognised models.
    Cost formula: (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    """
    pricing = get_pricing(provider_id, model_id)
    if pricing is None:
        return 0.0
    input_rate, output_rate = pricing
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def format_cost(cost_usd: float) -> str:
    """Format a cost value for display in the UI.

    Examples:
        0.000012  → "$0.000012"
        0.0042    → "$0.0042"
        1.234567  → "$1.2346"
    """
    if cost_usd == 0.0:
        return "$0.00"
    if cost_usd < 0.0001:
        return f"${cost_usd:.6f}"
    if cost_usd < 0.01:
        return f"${cost_usd:.4f}"
    return f"${cost_usd:.4f}"


def format_tokens(total: int) -> str:
    """Format a token count for display (e.g. 1,234,567 → '1.23M')."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.2f}M"
    if total >= 1_000:
        return f"{total / 1_000:.1f}K"
    return str(total)
