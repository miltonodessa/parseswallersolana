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

    async def _rpc(self, method: str, params: list) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params,
        }
        session = await self._get_session()
        try:
            async with session.post(self.rpc_url, json=payload) as resp:
                data = await resp.json()
                if "error" in data:
                    logger.warning("RPC error for %s: %s", method, data["error"])
                    return None
                return data.get("result")
        except Exception as e:
            logger.error("RPC request failed (%s): %s", method, e)
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
        self, addresses: list[str], tx_type: str = "SWAP"
    ) -> list[dict]:
        """Fetch enriched transaction history via Helius."""
        if not settings.HELIUS_API_KEY:
            return []
        session = await self._get_session()
        url = f"{HELIUS_BASE_URL}/addresses/{','.join(addresses)}/transactions?api-key={settings.HELIUS_API_KEY}&type={tx_type}&limit=100"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            logger.error("Helius transactions error: %s", e)
            return []

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
