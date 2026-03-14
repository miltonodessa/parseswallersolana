"""Parse token holders and extract unique trader wallets."""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import settings
from .rpc_client import SolanaRPCClient

logger = logging.getLogger(__name__)


@dataclass
class TokenHolder:
    wallet: str
    token_account: str
    amount: float
    ui_amount: float
    percentage: float = 0.0


@dataclass
class TokenInfo:
    mint: str
    name: str = ""
    symbol: str = ""
    decimals: int = 6
    supply: float = 0.0
    holders: list[TokenHolder] = field(default_factory=list)
    dev_wallet: Optional[str] = None


class TokenParser:
    def __init__(self, rpc: SolanaRPCClient):
        self.rpc = rpc

    async def get_token_info(self, mint: str) -> TokenInfo:
        """Fetch basic token info and supply."""
        info = TokenInfo(mint=mint)
        supply_data = await self.rpc.get_token_supply(mint)
        if supply_data:
            info.decimals = supply_data.get("decimals", 6)
            info.supply = float(supply_data.get("uiAmount", 0) or 0)

        if settings.HELIUS_API_KEY:
            overview = await self.rpc.birdeye_token_overview(mint)
            info.name = overview.get("name", "")
            info.symbol = overview.get("symbol", "")

        return info

    async def get_token_holders(self, mint: str, max_holders: int = None) -> list[TokenHolder]:
        """Return top token holders with their wallet addresses."""
        max_holders = max_holders or settings.MAX_HOLDERS_TO_PARSE
        holders: list[TokenHolder] = []

        if settings.HELIUS_API_KEY:
            holders = await self._get_holders_helius(mint, max_holders)
        else:
            holders = await self._get_holders_rpc(mint, max_holders)

        return holders

    async def _get_holders_rpc(self, mint: str, max_holders: int) -> list[TokenHolder]:
        """Fallback: use getTokenLargestAccounts (limited to top 20)."""
        raw = await self.rpc.get_token_largest_accounts(mint)
        supply_data = await self.rpc.get_token_supply(mint)
        total_supply = float((supply_data or {}).get("uiAmount", 1) or 1)

        holders = []
        for item in raw[:max_holders]:
            token_account = item.get("address", "")
            ui_amount = float(item.get("uiAmount", 0) or 0)
            amount = float(item.get("amount", 0) or 0)

            wallet = await self._resolve_wallet_from_token_account(token_account)
            if wallet:
                pct = (ui_amount / total_supply * 100) if total_supply > 0 else 0
                holders.append(TokenHolder(
                    wallet=wallet,
                    token_account=token_account,
                    amount=amount,
                    ui_amount=ui_amount,
                    percentage=pct,
                ))
        return holders

    async def _get_holders_helius(self, mint: str, max_holders: int) -> list[TokenHolder]:
        """Use Helius getTokenAccounts for full holder list."""
        holders = []
        page = 1
        supply_data = await self.rpc.get_token_supply(mint)
        total_supply = float((supply_data or {}).get("uiAmount", 1) or 1)

        while len(holders) < max_holders:
            result = await self.rpc.helius_get_token_holders(mint, page)
            accounts = result.get("token_accounts", [])
            if not accounts:
                break

            for acc in accounts:
                wallet = acc.get("owner", "")
                token_account = acc.get("address", "")
                amount = float(acc.get("amount", 0) or 0)
                decimals = acc.get("decimals", 6)
                ui_amount = amount / (10 ** decimals) if decimals else amount

                pct = (ui_amount / total_supply * 100) if total_supply > 0 else 0
                holders.append(TokenHolder(
                    wallet=wallet,
                    token_account=token_account,
                    amount=amount,
                    ui_amount=ui_amount,
                    percentage=pct,
                ))

            if len(holders) >= max_holders:
                break
            page += 1
            await asyncio.sleep(0.2)

        return holders[:max_holders]

    async def _resolve_wallet_from_token_account(self, token_account: str) -> Optional[str]:
        """Resolve owner wallet from a token account address."""
        info = await self.rpc.get_account_info(token_account)
        if not info:
            return None
        try:
            parsed = info["data"]["parsed"]["info"]
            return parsed.get("owner")
        except (KeyError, TypeError):
            return None

    async def get_dev_wallet(self, mint: str) -> Optional[str]:
        """Try to find the deployer/dev wallet for a token."""
        sigs = await self.rpc.get_signatures_for_address(mint, limit=10)
        if not sigs:
            return None

        oldest_sig = sigs[-1].get("signature")
        if not oldest_sig:
            return None

        tx = await self.rpc.get_transaction(oldest_sig)
        if not tx:
            return None

        try:
            accounts = tx["transaction"]["message"]["accountKeys"]
            for acc in accounts:
                if isinstance(acc, dict) and acc.get("signer") and acc.get("writable"):
                    return acc.get("pubkey")
            if isinstance(accounts[0], str):
                return accounts[0]
        except (KeyError, TypeError, IndexError):
            pass
        return None

    async def parse_token(self, mint: str) -> TokenInfo:
        """Full token parse: info + holders + dev wallet."""
        logger.info("Parsing token: %s", mint)
        token_info = await self.get_token_info(mint)

        holders_task = self.get_token_holders(mint)
        dev_task = self.get_dev_wallet(mint)

        token_info.holders, token_info.dev_wallet = await asyncio.gather(
            holders_task, dev_task
        )

        logger.info(
            "Token %s: %d holders found, dev=%s",
            mint, len(token_info.holders), token_info.dev_wallet
        )
        return token_info
