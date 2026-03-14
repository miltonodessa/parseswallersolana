"""Async Solana JSON-RPC client."""
import asyncio
import logging
from typing import Any, Optional

import aiohttp

import settings
from config import HELIUS_BASE_URL, BIRDEYE_BASE_URL

logger = logging.getLogger(__name__)


class SolanaRPCClient:
    def __init__(self, rpc_url: str = None):
        rpc_url = rpc_url or settings.SOLANA_RPC_URL
        self.rpc_url = rpc_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._id = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rpc(self, method: str, params: list, _retries: int = 4) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params,
        }
        session = await self._get_session()
        delay = 2.0
        for attempt in range(_retries + 1):
            try:
                async with session.post(self.rpc_url, json=payload) as resp:
                    data = await resp.json()
                    if "error" in data:
                        code = data["error"].get("code") if isinstance(data["error"], dict) else None
                        if code == -32429:  # rate limited
                            if attempt < _retries:
                                logger.debug("Rate limited on %s, retry %d in %.0fs", method, attempt + 1, delay)
                                await asyncio.sleep(delay)
                                delay *= 2
                                continue
                        logger.warning("RPC error for %s: %s", method, data["error"])
                        return None
                    return data.get("result")
            except Exception as e:
                if attempt < _retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                logger.error("RPC request failed (%s): %s", method, e)
                return None
        return None

    async def get_token_largest_accounts(self, mint: str) -> list[dict]:
        result = await self._rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        if result:
            return result.get("value", [])
        return []

    async def get_token_accounts_by_owner(self, owner: str, mint: str) -> list[dict]:
        result = await self._rpc(
            "getTokenAccountsByOwner",
            [
                owner,
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )
        if result:
            return result.get("value", [])
        return []

    async def get_account_info(self, pubkey: str) -> Optional[dict]:
        return await self._rpc(
            "getAccountInfo",
            [pubkey, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )

    async def get_token_supply(self, mint: str) -> Optional[dict]:
        result = await self._rpc("getTokenSupply", [mint])
        if result:
            return result.get("value")
        return None

    async def get_signatures_for_address(
        self, address: str, limit: int = 100, before: Optional[str] = None
    ) -> list[dict]:
        params: list = [address, {"limit": limit, "commitment": "confirmed"}]
        if before:
            params[1]["before"] = before
        result = await self._rpc("getSignaturesForAddress", params)
        return result or []

    async def get_transaction(self, signature: str) -> Optional[dict]:
        return await self._rpc(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )

    async def get_program_accounts(self, program: str, filters: list) -> list[dict]:
        result = await self._rpc(
            "getProgramAccounts",
            [program, {"encoding": "jsonParsed", "filters": filters}],
        )
        return result or []

    # ------------------------------------------------------------------
    # Helius enhanced APIs
    # ------------------------------------------------------------------

    async def helius_get_token_holders(self, mint: str, page: int = 1) -> dict:
        """Use Helius getTokenAccounts to list all holders."""
        if not settings.HELIUS_API_KEY:
            return {}
        session = await self._get_session()
        url = f"https://mainnet.helius-rpc.com/?api-key={settings.HELIUS_API_KEY}"
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "getTokenAccounts",
            "params": {"mint": mint, "page": page, "limit": 1000},
        }
        try:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                return data.get("result", {})
        except Exception as e:
            logger.error("Helius getTokenAccounts error: %s", e)
            return {}

    async def helius_get_parsed_transactions(
        self,
        addresses: list[str],
        tx_type: str = "SWAP",
        since_ts: int = 0,
        max_pages: int = 5,
    ) -> list[dict]:
        """
        Fetch enriched transaction history via Helius.

        Paginates until `since_ts` is reached or `max_pages` exhausted.
        `since_ts` is a unix timestamp — transactions older than this are dropped.
        """
        if not settings.HELIUS_API_KEY:
            return []
        session = await self._get_session()
        base = (
            f"{HELIUS_BASE_URL}/addresses/{','.join(addresses)}/transactions"
            f"?api-key={settings.HELIUS_API_KEY}&type={tx_type}&limit=100"
        )
        all_txs: list[dict] = []
        before_sig: str = ""

        for _ in range(max_pages):
            url = base + (f"&before={before_sig}" if before_sig else "")
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        break
                    page: list[dict] = await resp.json()
            except Exception as e:
                logger.error("Helius transactions error: %s", e)
                break

            if not page:
                break

            # Filter by time window and collect
            stop = False
            for tx in page:
                ts = tx.get("timestamp", 0) or 0
                if since_ts and ts < since_ts:
                    stop = True
                    break
                all_txs.append(tx)

            if stop or len(page) < 100:
                break

            # Cursor for next page = signature of last tx in this page
            before_sig = page[-1].get("signature", "")
            if not before_sig:
                break

        return all_txs

    # ------------------------------------------------------------------
    # Birdeye wallet analytics
    # ------------------------------------------------------------------

    async def birdeye_wallet_portfolio(self, wallet: str) -> dict:
        if not settings.BIRDEYE_API_KEY:
            return {}
        session = await self._get_session()
        url = f"{BIRDEYE_BASE_URL}/v1/wallet/token_list?wallet={wallet}"
        headers = {"X-API-KEY": settings.BIRDEYE_API_KEY, "x-chain": "solana"}
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
                return {}
        except Exception as e:
            logger.error("Birdeye portfolio error: %s", e)
            return {}

    async def birdeye_wallet_gains(self, wallet: str) -> dict:
        if not settings.BIRDEYE_API_KEY:
            return {}
        session = await self._get_session()
        url = f"{BIRDEYE_BASE_URL}/v1/wallet/gain?wallet={wallet}"
        headers = {"X-API-KEY": settings.BIRDEYE_API_KEY, "x-chain": "solana"}
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
                return {}
        except Exception as e:
            logger.error("Birdeye gains error: %s", e)
            return {}

    async def birdeye_token_overview(self, mint: str) -> dict:
        if not settings.BIRDEYE_API_KEY:
            return {}
        session = await self._get_session()
        url = f"{BIRDEYE_BASE_URL}/defi/token_overview?address={mint}"
        headers = {"X-API-KEY": settings.BIRDEYE_API_KEY, "x-chain": "solana"}
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
                return {}
        except Exception as e:
            logger.error("Birdeye token overview error: %s", e)
            return {}
