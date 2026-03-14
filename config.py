import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

MAX_HOLDERS_TO_PARSE = int(os.getenv("MAX_HOLDERS_TO_PARSE", "500"))
MIN_ROI_FILTER = float(os.getenv("MIN_ROI_FILTER", "50"))
MIN_WINRATE_FILTER = float(os.getenv("MIN_WINRATE_FILTER", "50"))
MIN_TRADES_FILTER = int(os.getenv("MIN_TRADES_FILTER", "5"))
RESULTS_DIR = os.getenv("RESULTS_DIR", "results")

HELIUS_BASE_URL = "https://api.helius.xyz/v0"
BIRDEYE_BASE_URL = "https://public-api.birdeye.so"

# Pump.fun program ID
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
# Raydium AMM program
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
