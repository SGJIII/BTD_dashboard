"""Arbiter Dashboard v2 — all constants, defaults, and configuration."""

import os
from typing import TypedDict

# ── Single User Input ─────────────────────────────────────────────────────────
DEFAULT_BUDGET = 640_000

# ── Risk Parameters ───────────────────────────────────────────────────────────
COLLATERAL_FRACTION = 0.35          # 15% adverse + 10% maint + 10% buffer
EMERGENCY_FLOOR = 50_000            # minimum emergency reserve
EMERGENCY_PCT = 0.08                # 8% of budget; EMERGENCY = max(FLOOR, 0.08*B)
OPS_RESERVE = 5_000                 # kept inside Coinbase bucket

# ── Multi-Asset Portfolio Limits ──────────────────────────────────────────────
MAX_NAMES = 6
MAX_CONCENTRATION = 0.50            # no single asset > 50% of H_max
MIN_TICKET_USD = 15_000             # DEPRECATED: kept for backward compat
MIN_TICKET_BUDGET_PCT = 0.02        # DEPRECATED: kept for backward compat
ALLOCATION_DUST_USD = 100           # skip allocations below this (noise floor)

# ── Soft Caps ─────────────────────────────────────────────────────────────────
OI_CAP_FRACTION = 0.05              # 5% of OI_USD
VOLUME_CAP_FRACTION = 0.10          # 10% of 24h volume
MAX_IMPACT_PCT = 0.0025             # 0.25% max slippage from L2 book walk

# ── Hard Gate ─────────────────────────────────────────────────────────────────
MIN_MAX_LEVERAGE = 10               # market must support >= 10x

# ── Dual EMA (8-hour epochs) ─────────────────────────────────────────────────
EMA_3D_EPOCHS = 9                   # 3 days × 3 eight-hour epochs
EMA_7D_EPOCHS = 21                  # 7 days × 3 eight-hour epochs
EMA_3D_ALPHA = 2 / (EMA_3D_EPOCHS + 1)   # 0.2
EMA_7D_ALPHA = 2 / (EMA_7D_EPOCHS + 1)   # ~0.0909
SEASONALITY_LOOKBACK_EPOCHS = 84    # 28 days × 3 epochs

# ── Weekend Forecast Weights ──────────────────────────────────────────────────
WEEKDAY_W3D = 0.70
WEEKDAY_W7D = 0.30
WEEKEND_W3D = 0.45
WEEKEND_W7D = 0.55

# ── Fees & Drag ───────────────────────────────────────────────────────────────
TAKER_FEE_PCT = 0.00045             # 4.5 bps (conservative tier-0)
REBALANCES_PER_YEAR = 365 / 7       # ~52 (one round trip per week)
# fee_drag_apr    = 2 * TAKER_FEE_PCT * REBALANCES_PER_YEAR * 100
# slip_drag_apr_i = 2 * impact_at_alloc_i * REBALANCES_PER_YEAR * 100

# ── Coinbase & Insurance ──────────────────────────────────────────────────────
DEFAULT_COINBASE_APR = 3.50         # percent
DEFAULT_INSURANCE_BUDGET_PCT = 1.50 # percent of budget

# ── Hyperliquid DEX Config ────────────────────────────────────────────────────
HL_TRADFI_DEX = "xyz"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# ── Stock-Only Mode ──────────────────────────────────────────────────────────
STOCK_ONLY_MODE = True                # when True, only equity perps are eligible

# ── Hedge Mapping ─────────────────────────────────────────────────────────────
# xyz coin → equity/ETF hedge symbol for delta-neutral pairing.
# Only coins with a mapping can receive allocation.
HEDGE_MAP = {
    # Direct equities
    "xyz:AAPL": "AAPL", "xyz:AMD": "AMD", "xyz:AMZN": "AMZN",
    "xyz:BABA": "BABA", "xyz:COIN": "COIN", "xyz:COST": "COST",
    "xyz:CRCL": "CRCL", "xyz:CRWV": "CRWV", "xyz:GME": "GME",
    "xyz:GOOGL": "GOOGL", "xyz:HOOD": "HOOD", "xyz:INTC": "INTC",
    "xyz:LLY": "LLY", "xyz:META": "META", "xyz:MSFT": "MSFT",
    "xyz:MSTR": "MSTR", "xyz:MU": "MU", "xyz:NFLX": "NFLX",
    "xyz:NVDA": "NVDA", "xyz:ORCL": "ORCL", "xyz:PLTR": "PLTR",
    "xyz:RIVN": "RIVN", "xyz:SNDK": "SNDK",
    "xyz:TSLA": "TSLA", "xyz:TSM": "TSM", "xyz:URNM": "URNM",
    # Commodity → ETF proxies
    "xyz:GOLD": "GLD", "xyz:SILVER": "SLV", "xyz:COPPER": "CPER",
    "xyz:PLATINUM": "PPLT", "xyz:PALLADIUM": "PALL",
    "xyz:URANIUM": "URA", "xyz:NATGAS": "UNG", "xyz:CL": "USO",
    # Index → ETF proxies
    "xyz:XYZ100": "SPY",
}

# Non-stock coins — excluded when STOCK_ONLY_MODE is True.
# These map to commodity ETFs or index ETFs, not individual equities.
NON_STOCK_COINS: set[str] = {
    "xyz:GOLD", "xyz:SILVER", "xyz:COPPER", "xyz:PLATINUM", "xyz:PALLADIUM",
    "xyz:URANIUM", "xyz:NATGAS", "xyz:CL", "xyz:XYZ100",
}

# ── NASDAQ Symbol Directories ─────────────────────────────────────────────────
NASDAQ_LISTED_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# ── Price Sanity ──────────────────────────────────────────────────────────────
MAX_PRICE_DIVERGENCE = 0.015        # 1.5% max divergence equity vs perp

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "arbiter.sqlite")

# ── Scheduling Intervals (seconds) ───────────────────────────────────────────
MARKET_REFRESH_INTERVAL = 60        # 60s for metaAndAssetCtxs + portfolio build
SCANNER_INTERVAL = 600              # 10 min for deep EMA refresh + alerts

# ── NYSE Trading Hours (ET) ──────────────────────────────────────────────────
NYSE_OPEN_HOUR = 9
NYSE_OPEN_MINUTE = 30
NYSE_CLOSE_HOUR = 16
NYSE_CLOSE_MINUTE = 0

# ── Rebalance Decision ───────────────────────────────────────────────────────
REBALANCE_HORIZON_DAYS = 7            # expected gain horizon
REBALANCE_COST_MULTIPLIER = 1.5       # switch only if gain > 1.5× cost
REBALANCE_FRICTION_BPS = 5            # additional friction buffer (bps)
REBALANCE_MIN_GAIN_USD = 1.0          # ignore gains below $1 (noise floor)

# ── Alert Deduplication ───────────────────────────────────────────────────────
OPPORTUNITY_DEDUP_HOURS = 6
OPPORTUNITY_REFIRE_APR_INCREASE = 10.0
CRITICAL_RESEND_MINUTES = 15
FUNDING_HURDLE_APR_POINTS = 20.0
FUNDING_APPROACH_APR_POINTS = 10.0

# ── Pushover (env var override) ───────────────────────────────────────────────
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")
PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")


class BudgetBuckets(TypedDict):
    budget: float
    emergency: float
    ops_reserve: float
    deployable: float
    h_max: float
    min_ticket: float


def compute_budget_buckets(budget: float) -> BudgetBuckets:
    """Compute budget-derived portfolio buckets with safe lower bounds."""
    b = max(float(budget or 0), 0.0)

    emergency_target = max(EMERGENCY_FLOOR, EMERGENCY_PCT * b)
    emergency = min(b, emergency_target)

    remaining = max(0.0, b - emergency)
    ops_reserve = min(OPS_RESERVE, remaining)
    deployable = remaining - ops_reserve

    h_max = deployable / (1 + COLLATERAL_FRACTION) if COLLATERAL_FRACTION > 0 else 0.0
    min_ticket = float(ALLOCATION_DUST_USD)  # waterfall: no hard floor, only dust

    return {
        "budget": b,
        "emergency": emergency,
        "ops_reserve": ops_reserve,
        "deployable": deployable,
        "h_max": h_max,
        "min_ticket": min_ticket,
    }


def normalize_coin(coin: str) -> str:
    """Normalize a coin identifier: trim whitespace, uppercase the symbol part.

    'xyz:sndk' -> 'xyz:SNDK', ' xyz:TSLA ' -> 'xyz:TSLA'
    """
    coin = coin.strip()
    if ":" in coin:
        prefix, symbol = coin.split(":", 1)
        return f"{prefix.strip().lower()}:{symbol.strip().upper()}"
    return coin.upper()
