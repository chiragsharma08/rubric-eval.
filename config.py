"""Global tunables. Everything here is meant to be changed per experiment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

MODEL = os.getenv("EVAL_MODEL", "claude-sonnet-5-5")
MAX_CONCURRENCY = int(os.getenv("EVAL_CONCURRENCY", "6"))
MAX_RETRIES = 4
RETRY_BASE_DELAY = 1.5

CACHE_DIR = Path(os.getenv("EVAL_CACHE_DIR", ".cache"))
RUNS_DIR = Path(os.getenv("EVAL_RUNS_DIR", "runs"))

# USD per million tokens, (input, output). Verify against current published
# pricing before quoting a cost figure to anyone.
PRICING = {
    "claude-opus-5-5": (15.00, 75.00),
    "claude-sonnet-5-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
DEFAULT_PRICING = (3.00, 15.00)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000
