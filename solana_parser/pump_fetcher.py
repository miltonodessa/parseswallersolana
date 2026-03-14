"""
Fetch tokens created today on pump.fun via their public API.

pump.fun API: https://frontend-api.pump.fun/coins
  sort=created_timestamp&order=DESC → newest first
  Paginate until created_timestamp < today_start_utc or max_tokens reached.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

logger = logging.getLogger(__name__)

_PUMP_API = "https://frontend-api.pump.fun/coins"
_PAGE_SIZE = 50   # pump.fun max per request


@dataclass
class PumpToken:
    mint: str
    name: str
    symbol: str
    created_timestamp: int          # unix seconds
    market_cap: float = 0.0         # SOL market cap
    usd_market_cap: float = 0.0
    complete: bool = False          # True = graduated to Raydium

    def created_dt(self) -> datetime:
        return datetime.fromtimestamp(self.created_timestamp, tz=timezone.utc)


async def fetch_today_tokens(
    max_tokens: int = 1000,
    min_usd_market_cap: float = 0.0,
    only_graduated: bool = False,
) -> list[PumpToken]:
    """
    Fetch all pump.fun tokens created today (UTC midnight → now).

    Args:
        max_tokens:          hard cap on results
        min_usd_market_cap:  skip tokens below this USD market cap
        only_graduated:      if True, return only tokens with complete=True
    Returns:
        List of PumpToken sorted newest-first.
    """
    today_start_ts_ms = int(
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    ) * 1000   # pump uses milliseconds

    tokens: list[PumpToken] = []
    offset = 0

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        while len(tokens) < max_tokens:
            try:
                params = {
                    "offset": offset,
                    "limit": _PAGE_SIZE,
                    "sort": "created_timestamp",
                    "order": "DESC",
                    "includeNsfw": "true",
                }
                async with session.get(_PUMP_API, params=params) as resp:
                    if resp.status != 200:
                        logger.error("pump.fun API returned HTTP %d", resp.status)
                        break
                    data = await resp.json()
            except Exception as e:
                logger.error("pump.fun fetch error: %s", e)
                break

            if not data:
                break

            reached_yesterday = False
            for coin in data:
                created_ms = coin.get("created_timestamp", 0) or 0
                if created_ms < today_start_ts_ms:
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
            await asyncio.sleep(0.3)   # be polite to pump.fun API

    logger.info("pump.fun: fetched %d tokens created today", len(tokens))
    return tokens[:max_tokens]
