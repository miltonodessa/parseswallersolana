"""Analyze wallet performance: ROI, WinRate, PnL from on-chain data."""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from .rpc_client import SolanaRPCClient
from config import HELIUS_API_KEY, BIRDEYE_API_KEY, MIN_ROI_FILTER, MIN_WINRATE_FILTER, MIN_TRADES_FILTER

logger = logging.getLogger(__name__)


@dataclass
class WalletStats:
    wallet: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0       # percentage 0-100
    total_pnl_usd: float = 0.0
    roi: float = 0.0             # percentage
    avg_trade_size_usd: float = 0.0
    tokens_traded: list[str] = field(default_factory=list)
    sol_balance: float = 0.0
    is_bot: bool = False
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "wallet": self.wallet,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "roi": round(self.roi, 2),
            "avg_trade_size_usd": round(self.avg_trade_size_usd, 2),
            "tokens_traded": len(self.tokens_traded),
            "sol_balance": round(self.sol_balance, 4),
            "is_bot": self.is_bot,
            "score": round(self.score, 2),
        }


class WalletAnalyzer:
    def __init__(self, rpc: SolanaRPCClient):
        self.rpc = rpc

    async def analyze_wallet(self, wallet: str) -> WalletStats:
        """Analyze a single wallet using available APIs."""
        stats = WalletStats(wallet=wallet)

        if BIRDEYE_API_KEY:
            stats = await self._analyze_via_birdeye(wallet, stats)
        elif HELIUS_API_KEY:
            stats = await self._analyze_via_helius(wallet, stats)
        else:
            stats = await self._analyze_via_rpc(wallet, stats)

        stats.score = self._compute_score(stats)
        return stats

    async def _analyze_via_birdeye(self, wallet: str, stats: WalletStats) -> WalletStats:
        gains = await self.rpc.birdeye_wallet_gains(wallet)
        if gains:
            stats.total_pnl_usd = float(gains.get("realizedPnL", 0) or 0)
            stats.roi = float(gains.get("roi", 0) or 0) * 100
            stats.winning_trades = int(gains.get("numWin", 0) or 0)
            stats.losing_trades = int(gains.get("numLoss", 0) or 0)
            stats.total_trades = stats.winning_trades + stats.losing_trades
            if stats.total_trades > 0:
                stats.win_rate = (stats.winning_trades / stats.total_trades) * 100

        portfolio = await self.rpc.birdeye_wallet_portfolio(wallet)
        if portfolio:
            items = portfolio.get("items", [])
            for item in items:
                symbol = item.get("symbol", "")
                if symbol == "SOL":
                    stats.sol_balance = float(item.get("uiAmount", 0) or 0)
                mint = item.get("address", "")
                if mint and mint not in stats.tokens_traded:
                    stats.tokens_traded.append(mint)

        return stats

    async def _analyze_via_helius(self, wallet: str, stats: WalletStats) -> WalletStats:
        """Analyze wallet from Helius enriched swap transactions."""
        txs = await self.rpc.helius_get_parsed_transactions([wallet], tx_type="SWAP")

        buys: dict[str, float] = {}
        sells: dict[str, float] = {}

        for tx in txs:
            events = tx.get("events", {})
            swap = events.get("swap", {})
            if not swap:
                continue

            token_in = swap.get("tokenInputs", [{}])
            token_out = swap.get("tokenOutputs", [{}])
            native_in = swap.get("nativeInput") or {}
            native_out = swap.get("nativeOutput") or {}

            # Detect buy (SOL -> token) or sell (token -> SOL)
            if native_in and token_out:
                sol_spent = float(native_in.get("amount", 0)) / 1e9
                for tok in token_out:
                    mint = tok.get("mint", "")
                    if mint:
                        buys[mint] = buys.get(mint, 0) + sol_spent
                        if mint not in stats.tokens_traded:
                            stats.tokens_traded.append(mint)

            elif token_in and native_out:
                sol_received = float(native_out.get("amount", 0)) / 1e9
                for tok in token_in:
                    mint = tok.get("mint", "")
                    if mint:
                        sells[mint] = sells.get(mint, 0) + sol_received

        # Rough PnL calculation (needs SOL price for USD)
        for mint in buys:
            bought = buys[mint]
            sold = sells.get(mint, 0)
            pnl_sol = sold - bought
            stats.total_pnl_usd += pnl_sol  # in SOL units; multiply by price for USD
            if sold > 0:
                stats.total_trades += 1
                if pnl_sol > 0:
                    stats.winning_trades += 1
                else:
                    stats.losing_trades += 1

        if stats.total_trades > 0:
            stats.win_rate = (stats.winning_trades / stats.total_trades) * 100

        total_bought = sum(buys.values())
        if total_bought > 0:
            stats.roi = (stats.total_pnl_usd / total_bought) * 100

        return stats

    async def _analyze_via_rpc(self, wallet: str, stats: WalletStats) -> WalletStats:
        """Minimal on-chain analysis without external APIs."""
        sigs = await self.rpc.get_signatures_for_address(wallet, limit=50)
        stats.total_trades = len(sigs)
        # Without a price oracle we cannot compute PnL/ROI accurately
        logger.warning("No API keys configured — limited analysis for %s", wallet)
        return stats

    def _compute_score(self, stats: WalletStats) -> float:
        """Compute a composite smart-wallet score (0–100)."""
        if stats.total_trades < MIN_TRADES_FILTER:
            return 0.0
        wr_score = min(stats.win_rate, 100) * 0.4
        roi_score = min(max(stats.roi, 0), 500) / 500 * 100 * 0.4
        volume_score = min(stats.total_trades, 100) / 100 * 100 * 0.2
        return wr_score + roi_score + volume_score

    def passes_filter(
        self,
        stats: WalletStats,
        min_roi: float = MIN_ROI_FILTER,
        min_winrate: float = MIN_WINRATE_FILTER,
        min_trades: int = MIN_TRADES_FILTER,
    ) -> bool:
        return (
            stats.total_trades >= min_trades
            and stats.win_rate >= min_winrate
            and stats.roi >= min_roi
            and not stats.is_bot
        )

    async def analyze_wallets_batch(
        self,
        wallets: list[str],
        concurrency: int = 5,
        progress_callback=None,
    ) -> list[WalletStats]:
        """Analyze a list of wallets with concurrency control."""
        semaphore = asyncio.Semaphore(concurrency)
        results = []
        done = 0

        async def _analyze(wallet: str):
            nonlocal done
            async with semaphore:
                try:
                    stats = await self.analyze_wallet(wallet)
                    results.append(stats)
                except Exception as e:
                    logger.error("Failed to analyze %s: %s", wallet, e)
                finally:
                    done += 1
                    if progress_callback:
                        await progress_callback(done, len(wallets))

        await asyncio.gather(*[_analyze(w) for w in wallets])
        return results
