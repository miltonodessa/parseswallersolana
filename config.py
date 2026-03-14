import os
from dotenv import load_dotenv

load_dotenv()

# Solana RPC
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Helius API (recommended — gives full holder lists, enriched txs)
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

# Birdeye API (wallet PnL/ROI/WinRate data)
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

# Max holders to scan per token
MAX_HOLDERS_TO_PARSE = int(os.getenv("MAX_HOLDERS_TO_PARSE", "500"))

# Output directory
RESULTS_DIR = os.getenv("RESULTS_DIR", "results")

# ---------------------------------------------------------------------------
# Default filter values (all overridable via CLI flags or .env)
# Based on Froggy v2 documentation recommendations
# ---------------------------------------------------------------------------

# ROI — minimum return on investment, %
FILTER_MIN_ROI = float(os.getenv("FILTER_MIN_ROI", "50"))

# WinRate — minimum win rate, %
FILTER_MIN_WINRATE = float(os.getenv("FILTER_MIN_WINRATE", "50"))

# Fast Trades — maximum allowed % of trades closed in < 3 minutes
# Above 15% signals rug-pull / bot behaviour
FILTER_MAX_FAST_TRADES_PCT = float(os.getenv("FILTER_MAX_FAST_TRADES_PCT", "15"))

# SMTB (Sold More Than Buy) — max % of "sell without buy" events
# Recommended ≤ 8–10%; 1 per 10 trades is acceptable
FILTER_MAX_SMTB_PCT = float(os.getenv("FILTER_MAX_SMTB_PCT", "10"))

# Balance — minimum SOL balance
FILTER_MIN_BALANCE_SOL = float(os.getenv("FILTER_MIN_BALANCE_SOL", "2"))

# Tokens Total — minimum number of different tokens traded
# < 3 = fresh/empty wallet, skip it
FILTER_MIN_TOKENS_TOTAL = int(os.getenv("FILTER_MIN_TOKENS_TOTAL", "3"))

# Trade Frequency — minimum trades per week
FILTER_MIN_TRADES_PER_WEEK = float(os.getenv("FILTER_MIN_TRADES_PER_WEEK", "1"))

# Minimum total trades (absolute floor)
FILTER_MIN_TOTAL_TRADES = int(os.getenv("FILTER_MIN_TOTAL_TRADES", "5"))

# Helius / Birdeye base URLs
HELIUS_BASE_URL = "https://api.helius.xyz/v0"
BIRDEYE_BASE_URL = "https://public-api.birdeye.so"
