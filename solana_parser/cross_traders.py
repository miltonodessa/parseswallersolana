"""Find wallets that traded multiple tokens — cross-trader analysis."""
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from .rpc_client import SolanaRPCClient
from .token_parser import TokenParser

logger = logging.getLogger(__name__)


@dataclass
class CrossTrader:
    wallet: str
    tokens: list[str] = field(default_factory=list)
    token_count: int = 0

    def to_dict(self) -> dict:
        return {
            "wallet": self.wallet,
            "tokens": self.tokens,
            "token_count": self.token_count,
        }


class CrossTraderFinder:
    def __init__(self, rpc: SolanaRPCClient):
        self.rpc = rpc
        self.token_parser = TokenParser(rpc)

    async def find_cross_traders(
        self, mints: list[str], min_token_count: int = 2
    ) -> list[CrossTrader]:
        """
        Given a list of token mints, find wallets that appear in multiple tokens.

        Args:
            mints: List of token mint addresses to scan.
            min_token_count: Minimum number of tokens a wallet must appear in.

        Returns:
            Sorted list of CrossTrader objects.
        """
        wallet_to_tokens: dict[str, list[str]] = defaultdict(list)

        for mint in mints:
            logger.info("Fetching holders for %s", mint)
            try:
                holders = await self.token_parser.get_token_holders(mint)
                for h in holders:
                    if h.wallet:
                        wallet_to_tokens[h.wallet].append(mint)
            except Exception as e:
                logger.error("Error parsing token %s: %s", mint, e)

        cross_traders = [
            CrossTrader(
                wallet=wallet,
                tokens=tokens,
                token_count=len(tokens),
            )
            for wallet, tokens in wallet_to_tokens.items()
            if len(tokens) >= min_token_count
        ]

        cross_traders.sort(key=lambda x: x.token_count, reverse=True)
        logger.info("Found %d cross-traders across %d tokens", len(cross_traders), len(mints))
        return cross_traders
