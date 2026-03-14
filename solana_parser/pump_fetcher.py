"""
Fetch tokens created today on pump.fun.

Strategy (tried in order):
  1. pump.fun REST API  (frontend-api.pump.fun)
     — blocked by Cloudflare from datacenter IPs; works from a home machine.
  2. On-chain RPC scan  (Solana getSignaturesForAddress + getTransaction)
     — works everywhere, but limited to the most recent N signatures.
     — identifies "create" txs via log message "Instruction: Create",
       extracts mint from postTokenBalances.

If the API is blocked, the RPC fallback returns the most recent tokens
created today (up to PUMP_RPC_SCAN_LIMIT transactions scanned).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# pump.fun bonding-curve program (Solana mainnet)
_PUMP_PROGRAM  = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_PUMP_API_URL  = "https://frontend-api.pump.fun/coins"
_PUMP_API_V2   = "https://frontend-api-v3.pump.fun/coins"
_PAGE_SIZE     = 50

# pump.fun bonding curve: graduates when 85 SOL are raised.
# Market cap at graduation ≈ 85 SOL × SOL_price × ~4.8 (fully-diluted factor).
# At SOL=$86 → migration MC ≈ $35 000 USD.
PUMP_GRADUATION_SOL = 85          # fixed on-chain threshold
PUMP_MC_MULTIPLIER  = 4.8         # empirical FDV/raised ratio at graduation

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"


async def fetch_sol_price() -> float:
    """
    Fetch current SOL/USD price from CoinGecko (free, no API key).
    Uses urllib in a thread so it works even when aiohttp DNS is restricted.
    """
    import asyncio, urllib.request, json as _json

    def _get_price() -> float:
        try:
            with urllib.request.urlopen(_COINGECKO_URL, timeout=10) as r:
                data = _json.load(r)
                return float(data["solana"]["usd"])
        except Exception:
            pass
        # aiohttp fallback
        return 0.0

    try:
        loop = asyncio.get_event_loop()
        price = await loop.run_in_executor(None, _get_price)
        if price:
            return price
    except Exception as e:
        logger.warning("Could not fetch SOL price: %s", e)

    # aiohttp fallback
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(_COINGECKO_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["solana"]["usd"])
    except Exception:
        pass

    return 0.0


def migration_threshold_usd(sol_price: float) -> float:
    """Calculate approximate USD market cap at pump.fun migration."""
    return PUMP_GRADUATION_SOL * sol_price * PUMP_MC_MULTIPLIER

# Max transactions to scan via RPC when the API is unavailable
PUMP_RPC_SCAN_LIMIT = 2000

# Browser-like headers to pass Cloudflare basic checks
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://pump.fun/",
    "Origin":          "https://pump.fun",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}


@dataclass
class PumpToken:
    mint: str
    name: str
    symbol: str
    created_timestamp: int          # unix seconds UTC
    market_cap: float = 0.0         # SOL market cap
    usd_market_cap: float = 0.0
    complete: bool = False          # True = graduated to Raydium

    def created_dt(self) -> datetime:
        return datetime.fromtimestamp(self.created_timestamp, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def fetch_today_tokens(
    max_tokens: int = 1000,
    min_usd_market_cap: float = 0.0,
    only_graduated: bool = False,
    rpc_url: str = None,
) -> list[PumpToken]:
    """
    Fetch all pump.fun tokens created today (UTC midnight → now).

    Tries the pump.fun REST API first; falls back to on-chain RPC scan
    if the API is unreachable (e.g. blocked by Cloudflare on server IPs).

    Returns a list of PumpToken sorted newest-first.
    """
    today_start = _today_start_ts()

    # ── Attempt 1: pump.fun REST API ────────────────────────────────────
    for api_url in (_PUMP_API_URL, _PUMP_API_V2):
        tokens = await _fetch_via_api(
            api_url, today_start, max_tokens, min_usd_market_cap, only_graduated
        )
        if tokens is not None:   # None = failed/blocked, [] = no tokens today
            logger.info("pump.fun API: %d tokens today via %s", len(tokens), api_url)
            return tokens
        logger.warning("pump.fun API unavailable: %s — trying next…", api_url)

    # ── Attempt 2: on-chain RPC scan ────────────────────────────────────
    logger.warning(
        "pump.fun API blocked (likely Cloudflare protecting server IPs). "
        "Falling back to on-chain Solana RPC scan. "
        "Note: this only returns the most recent tokens, not the full day. "
        "To get all today's tokens, run the parser from a home machine."
    )
    tokens = await _fetch_via_rpc(
        rpc_url=rpc_url,
        today_start=today_start,
        max_tokens=max_tokens,
        min_usd_market_cap=min_usd_market_cap,
        only_graduated=only_graduated,
    )
    logger.info("on-chain scan: %d pump.fun tokens found", len(tokens))
    return tokens


# ---------------------------------------------------------------------------
# Strategy 1: pump.fun REST API
# ---------------------------------------------------------------------------

async def _fetch_via_api(
    api_url: str,
    today_start_ts: int,
    max_tokens: int,
    min_usd_market_cap: float,
    only_graduated: bool,
) -> Optional[list[PumpToken]]:
    """
    Returns list on success (possibly empty), None on connection/auth failure.
    """
    today_start_ms = today_start_ts * 1000  # pump.fun uses ms timestamps
    tokens: list[PumpToken] = []
    offset = 0

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers=_BROWSER_HEADERS,
        ) as session:
            while len(tokens) < max_tokens:
                params = {
                    "offset":       offset,
                    "limit":        _PAGE_SIZE,
                    "sort":         "created_timestamp",
                    "order":        "DESC",
                    "includeNsfw":  "true",
                }
                try:
                    async with session.get(api_url, params=params) as resp:
                        if resp.status in (403, 429, 503, 530):
                            logger.debug("pump.fun API HTTP %d", resp.status)
                            return None
                        if resp.status != 200:
                            logger.debug("pump.fun API HTTP %d", resp.status)
                            return None
                        data = await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.debug("pump.fun API connection error: %s", e)
                    return None

                if not data or not isinstance(data, list):
                    break

                reached_yesterday = False
                for coin in data:
                    created_ms = coin.get("created_timestamp", 0) or 0
                    if created_ms < today_start_ms:
                        reached_yesterday = True
                        break

                    mint = (coin.get("mint") or "").strip()
                    if not mint:
                        continue

                    usd_mc = float(coin.get("usd_market_cap", 0) or 0)
                    if usd_mc < min_usd_market_cap:
                        continue

                    graduated = bool(coin.get("complete", False))
                    if only_graduated and not graduated:
                        continue

                    tokens.append(PumpToken(
                        mint=mint,
                        name=coin.get("name", "") or "",
                        symbol=coin.get("symbol", "") or "",
                        created_timestamp=created_ms // 1000,
                        market_cap=float(coin.get("market_cap", 0) or 0),
                        usd_market_cap=usd_mc,
                        complete=graduated,
                    ))

                if reached_yesterday or len(data) < _PAGE_SIZE:
                    break

                offset += _PAGE_SIZE
                await asyncio.sleep(0.3)

    except Exception as e:
        logger.debug("pump.fun API unexpected error: %s", e)
        return None

    return tokens[:max_tokens]


# ---------------------------------------------------------------------------
# Strategy 2: On-chain Solana RPC scan
# ---------------------------------------------------------------------------

async def _fetch_via_rpc(
    rpc_url: str,
    today_start: int,
    max_tokens: int,
    min_usd_market_cap: float,
    only_graduated: bool,
) -> list[PumpToken]:
    """
    Scan recent pump.fun program transactions via public Solana RPC.
    Identifies "create" transactions by log messages and extracts mints
    from postTokenBalances.

    Limited to PUMP_RPC_SCAN_LIMIT transaction signature lookups.
    """
    import settings
    rpc_url = rpc_url or settings.SOLANA_RPC_URL

    # ── Step 1: collect today's valid signatures ─────────────────────
    all_sigs: list[dict] = []
    before: Optional[str] = None
    scan_limit = PUMP_RPC_SCAN_LIMIT

    print(f"      [RPC scan] Собираю подписи из программы pump.fun (лимит: {scan_limit})...")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while len(all_sigs) < scan_limit:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [_PUMP_PROGRAM, {
                    "limit": 1000,
                    "commitment": "finalized",
                    **({"before": before} if before else {}),
                }],
            }
            try:
                async with session.post(rpc_url, json=payload) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.error("RPC getSignaturesForAddress error: %s", e)
                break

            page = data.get("result", [])
            if not page:
                break

            # Only keep non-errored transactions from today
            for s in page:
                ts = s.get("blockTime") or 0
                if ts < today_start:
                    # Reached yesterday — stop scanning
                    all_sigs.extend(
                        x for x in page
                        if (x.get("blockTime") or 0) >= today_start and not x.get("err")
                    )
                    scan_limit = 0   # signal to stop outer loop
                    break
                if not s.get("err"):
                    all_sigs.append(s)

            if scan_limit == 0 or len(page) < 1000:
                break

            before = page[-1]["signature"]
            await asyncio.sleep(0.4)

    print(f"      [RPC scan] Найдено {len(all_sigs)} транзакций за сегодня → ищу создания токенов...")

    if not all_sigs:
        return []

    # ── Step 2: batch-fetch transactions, extract mints ─────────────
    semaphore = asyncio.Semaphore(3)  # be gentle with public RPC
    results: list[PumpToken] = []
    lock = asyncio.Lock()
    found = [0]

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:

        async def _process_sig(sig_info: dict):
            sig = sig_info["signature"]
            ts  = sig_info.get("blockTime", 0)
            async with semaphore:
                try:
                    payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "finalized",
                        }],
                    }
                    async with session.post(rpc_url, json=payload) as resp:
                        data = await resp.json()
                    tx = data.get("result")
                    if not tx:
                        return

                    meta = tx.get("meta") or {}
                    if meta.get("err"):
                        return

                    logs = meta.get("logMessages") or []
                    is_create = any("Instruction: Create" in l for l in logs)
                    if not is_create:
                        return

                    # Extract mint from postTokenBalances
                    ptb = meta.get("postTokenBalances") or []
                    mints_seen: set[str] = set()
                    for bal in ptb:
                        mint = bal.get("mint", "")
                        if mint and mint not in mints_seen:
                            mints_seen.add(mint)
                            token = PumpToken(
                                mint=mint,
                                name="",
                                symbol="",
                                created_timestamp=ts,
                                market_cap=0.0,
                                usd_market_cap=0.0,
                                complete=False,
                            )
                            async with lock:
                                results.append(token)
                                found[0] += 1
                except Exception as e:
                    logger.debug("getTransaction %s error: %s", sig[:20], e)
                finally:
                    await asyncio.sleep(0.1)

        tasks = [_process_sig(s) for s in all_sigs]
        total = len(tasks)
        done_count = [0]

        async def _wrap(coro, idx):
            await coro
            done_count[0] += 1
            if done_count[0] % 50 == 0 or done_count[0] == total:
                print(
                    f"\r      [RPC scan] {_progress_bar(done_count[0], total)}  "
                    f"токенов: {found[0]}",
                    end="", flush=True,
                )

        await asyncio.gather(*[_wrap(t, i) for i, t in enumerate(tasks)])
        print()

    # Deduplicate by mint (a mint can appear in multiple balances)
    seen: dict[str, PumpToken] = {}
    for t in results:
        if t.mint not in seen:
            seen[t.mint] = t

    tokens = list(seen.values())
    tokens.sort(key=lambda x: x.created_timestamp, reverse=True)

    # Apply filters
    if min_usd_market_cap > 0:
        tokens = [t for t in tokens if t.usd_market_cap >= min_usd_market_cap]
    if only_graduated:
        tokens = [t for t in tokens if t.complete]

    return tokens[:max_tokens]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_start_ts() -> int:
    return int(
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )

def _progress_bar(done: int, total: int, width: int = 25) -> str:
    pct = done / total if total else 0
    filled = int(pct * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:5.1%}"
