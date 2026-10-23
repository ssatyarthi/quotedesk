"""Paths, model choice and buyer-editable assumptions."""
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RFX_SEED = DATA / "rfx" / "rfx_seed.json"
HISTORY = DATA / "history" / "contract_FY2025-26_bharat.csv"
RESPONSES = DATA / "responses"
CACHE = ROOT / "cache"
EXTRACTIONS = CACHE / "extractions"
OVERRIDES = CACHE / "overrides.json"
DECISIONS = CACHE / "flag_decisions.json"
USAGE_LOG = CACHE / "usage.jsonl"
OUTBOX = ROOT / "outbox"
for p in (CACHE, EXTRACTIONS, OUTBOX):
    p.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# First model that works for the account is used; override with QD_MODEL in .env
MODEL_CANDIDATES = [m for m in [os.getenv("QD_MODEL"), "claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-4-5"] if m]

# Bumping this invalidates cached extractions (so prompt changes are re-run)
EXTRACTION_VERSION = "2026-10-23.1"


@dataclass
class Assumptions:
    """Things the system cannot know and the buyer must own. Always shown on screen."""
    usd_inr: float = 88.00
    usd_inr_asof: str = "demo assumption - set today's RBI reference rate"
    freight_inr_per_kg: float = 2.00  # used only for vendors who quote "freight extra"
    award_date: str = field(default_factory=lambda: date.today().isoformat())
    apply_conditional_discounts: bool = False
    include_alternates: bool = False
    sanity_ratio_high: float = 1.8  # price vs peer-median above this => suspected unit error
    sanity_ratio_low: float = 0.55
    # Payment terms (Decision 4): later payment is better unless an early-payment discount beats holding the cash
    value_payment_terms: bool = True
    cost_of_money_pct: float = 10.0   # buyer's annual cost of working capital
    reference_payment_days: int = 60  # what the RFx asked for
