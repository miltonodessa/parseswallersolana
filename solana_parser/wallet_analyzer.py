"""
Wallet analyzer — computes all Froggy v2 metrics and applies filters.

Metrics tracked per wallet:
  - ROI                  : return on investment, %
  - WinRate              : % of profitable closed trades
  - Fast Trades %        : trades closed in < 3 minutes (rug signal if > 15%)
  - SMTB %               : "Sold More Than Bought" ratio (signal if > 10%)
  - Balance SOL          : current SOL balance
  - Tokens Total         : number of distinct tokens traded
  - Trade Frequency/week : average trades per week
  - Total Trades         : absolute trade count
  - PnL USD              : total realized profit/loss
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import settings
from .rpc_client import SolanaRPCClient

logger = logging.getLogger(__name__)

# Trades shorter than this (seconds) are counted as "fast trades"
FAST_TRADE_THRESHOLD_SEC = 180


@dataclass
class WalletStats:
    wallet: str

    # Core metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0           # %
    total_pnl_usd: float = 0.0
    roi: float = 0.0                 # %
    avg_trade_size_sol: float = 0.0

    # Froggy-specific filters
    fast_trades_pct: float = 0.0    # % of trades < 3 min
    smtb_pct: float = 0.0           # % "sold more than bought" events
    sol_balance: float = 0.0
    tokens_total: int = 0            # distinct tokens traded
    trades_per_week: float = 0.0

    # Internals
    tokens_traded: list[str] = field(default_factory=list)
    first_trade_ts: Optional[int] = None   # unix timestamp
    last_trade_ts: Optional[int] = None

    # Composite score
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "wallet": self.wallet,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_%": round(self.win_rate, 2),
            "roi_%": round(self.roi, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "avg_trade_size_sol": round(self.avg_trade_size_sol, 4),
            "fast_trades_%": round(self.fast_trades_pct, 2),
            "smtb_%": round(self.smtb_pct, 2),
            "sol_balance": round(self.sol_balance, 4),
            "tokens_total": self.tokens_total,
            "trades_per_week": round(self.trades_per_week, 2),
            "score": round(self.score, 2),
        }

    def filter_report(self, filters: "WalletFilters") -> dict[str, bool]:
        """Return a per-filter pass/fail dict for debugging."""
        return {
            "roi": self.roi >= filters.min_roi,
            "win_rate": self.win_rate >= filters.min_winrate,
            "fast_trades": self.fast_trades_pct <= filters.max_fast_trades_pct,
            "smtb": self.smtb_pct <= filters.max_smtb_pct,
            "balance": self.sol_balance >= filters.min_balance_sol,
            "tokens_total": self.tokens_total >= filters.min_tokens_total,
            "trades_per_week": self.trades_per_week >= filters.min_trades_per_week,
            "total_trades": self.total_trades >= filters.min_total_trades,
        }


@dataclass
class WalletFilters:
    """All Froggy v2 filter parameters — fully configurable."""
    min_roi: float = None
    min_winrate: float = None
    max_fast_trades_pct: float = None
    max_smtb_pct: float = None
    min_balance_sol: float = None
    min_tokens_total: int = None
    min_trades_per_week: float = None
    min_total_trades: int = None

    def __post_init__(self):
        if self.min_roi is None:           self.min_roi = settings.MIN_ROI
        if self.min_winrate is None:       self.min_winrate = settings.MIN_WINRATE
        if self.max_fast_trades_pct is None: self.max_fast_trades_pct = settings.MAX_FAST_TRADES_PCT
        if self.max_smtb_pct is None:      self.max_smtb_pct = settings.MAX_SMTB_PCT
        if self.min_balance_sol is None:   self.min_balance_sol = settings.MIN_BALANCE_SOL
        if self.min_tokens_total is None:  self.min_tokens_total = settings.MIN_TOKENS_TOTAL
        if self.min_trades_per_week is None: self.min_trades_per_week = settings.MIN_TRADES_PER_WEEK
        if self.min_total_trades is None:  self.min_total_trades = settings.MIN_TOTAL_TRADES

    def describe(self) -> str:
        return (
            f"ROI≥{self.min_roi}%  WR≥{self.min_winrate}%  "
            f"FastTrades≤{self.max_fast_trades_pct}%  SMTB≤{self.max_smtb_pct}%  "
            f"Balance≥{self.min_balance_sol}SOL  Tokens≥{self.min_tokens_total}  "
            f"Freq≥{self.min_trades_per_week}/wk  Trades≥{self.min_total_trades}"
        )


class WalletAnalyzer:
    def __init__(self, rpc: SolanaRPCClient):
        self.rpc = rpc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_wallet(self, wallet: str) -> WalletStats:
        stats = WalletStats(wallet=wallet)

        if settings.BIRDEYE_API_KEY:
            stats = await self._fill_from_birdeye(wallet, stats)

        if settings.HELIUS_API_KEY:
            stats = await self._fill_from_helius(wallet, stats)
        elif not settings.BIRDEYE_API_KEY:
            stats = await self._fill_from_rpc(wallet, stats)

        # SOL balance (try both APIs)
        if stats.sol_balance == 0:
            stats.sol_balance = await self._get_sol_balance(wallet)

        stats.tokens_total = len(stats.tokens_traded)
        stats.score = self._compute_score(stats)
        return stats

    def passes_filter(self, stats: WalletStats, filters: WalletFilters) -> bool:
        return all(stats.filter_report(filters).values())

    async def analyze_wallets_batch(
        self,
        wallets: list[str],
        concurrency: int = 5,
        progress_callback=None,
    ) -> list[WalletStats]:
        semaphore = asyncio.Semaphore(concurrency)
        results: list[WalletStats] = []
        done = 0

        async def _analyze(wallet: str):
            nonlocal done
            async with semaphore:
                try:
                    s = await self.analyze_wallet(wallet)
                    results.append(s)
                except Exception as e:
                    logger.error("analyze %s: %s", wallet, e)
                finally:
                    done += 1
                    if progress_callback:
                        await progress_callback(done, len(wallets))

        await asyncio.gather(*[_analyze(w) for w in wallets])
        return results

    # ------------------------------------------------------------------
    # Birdeye data source
    # ------------------------------------------------------------------

    async def _fill_from_birdeye(self, wallet: str, stats: WalletStats) -> WalletStats:
        gains = await self.rpc.birdeye_wallet_gains(wallet)
        if gains:
            stats.total_pnl_usd = float(gains.get("realizedPnL", 0) or 0)
            roi_raw = gains.get("roi", 0) or 0
            stats.roi = float(roi_raw) * 100 if abs(float(roi_raw)) <= 10 else float(roi_raw)
            stats.winning_trades = int(gains.get("numWin", 0) or 0)
            stats.losing_trades = int(gains.get("numLoss", 0) or 0)
            stats.total_trades = stats.winning_trades + stats.losing_trades
            if stats.total_trades > 0:
                stats.win_rate = (stats.winning_trades / stats.total_trades) * 100

        portfolio = await self.rpc.birdeye_wallet_portfolio(wallet)
        if portfolio:
            for item in portfolio.get("items", []):
                mint = item.get("address", "")
                symbol = item.get("symbol", "")
                if symbol == "SOL":
                    stats.sol_balance = float(item.get("uiAmount", 0) or 0)
                if mint and mint not in stats.tokens_traded:
                    stats.tokens_traded.append(mint)

        return stats

    # ------------------------------------------------------------------
    # Helius data source — enriched SWAP transactions
    # ------------------------------------------------------------------

    async def _fill_from_helius(self, wallet: str, stats: WalletStats) -> WalletStats:
        txs = await self.rpc.helius_get_parsed_transactions([wallet], tx_type="SWAP")
        if not txs:
            return stats

        # {mint: [(buy_sol, ts), ...]}
        buys: dict[str, list[tuple[float, int]]] = {}
        # {mint: [(sell_sol, ts), ...]}
        sells: dict[str, list[tuple[float, int]]] = {}
        timestamps: list[int] = []

        for tx in txs:
            ts = tx.get("timestamp", 0) or 0
            timestamps.append(ts)
            events = tx.get("events", {})
            swap = events.get("swap", {})
            if not swap:
                continue

            native_in = swap.get("nativeInput") or {}
            native_out = swap.get("nativeOutput") or {}
            token_in = swap.get("tokenInputs") or []
            token_out = swap.get("tokenOutputs") or []

            # Buy: SOL → token
            if native_in and token_out:
                sol_spent = float(native_in.get("amount", 0)) / 1e9
                for tok in token_out:
                    mint = tok.get("mint", "")
                    if mint:
                        buys.setdefault(mint, []).append((sol_spent, ts))
                        if mint not in stats.tokens_traded:
                            stats.tokens_traded.append(mint)

            # Sell: token → SOL
            elif token_in and native_out:
                sol_received = float(native_out.get("amount", 0)) / 1e9
                for tok in token_in:
                    mint = tok.get("mint", "")
                    if mint:
                        sells.setdefault(mint, []).append((sol_received, ts))
                        if mint not in stats.tokens_traded:
                            stats.tokens_traded.append(mint)

        # Compute PnL, WinRate, FastTrades, SMTB
        stats = self._compute_helius_metrics(stats, buys, sells, timestamps)
        return stats

    def _compute_helius_metrics(
        self,
        stats: WalletStats,
        buys: dict,
        sells: dict,
        timestamps: list[int],
    ) -> WalletStats:
        fast_trade_count = 0
        smtb_count = 0
        all_trades = 0
        total_bought_sol = 0.0
        pnl_sol = 0.0

        all_mints = set(list(buys.keys()) + list(sells.keys()))

        for mint in all_mints:
            buy_list = sorted(buys.get(mint, []), key=lambda x: x[1])
            sell_list = sorted(sells.get(mint, []), key=lambda x: x[1])

            # SMTB: sells without a matching buy
            if len(sell_list) > len(buy_list):
                smtb_count += len(sell_list) - len(buy_list)

            # Match buys to sells for PnL + fast trades
            for i, (sell_sol, sell_ts) in enumerate(sell_list):
                if i < len(buy_list):
                    buy_sol, buy_ts = buy_list[i]
                    total_bought_sol += buy_sol
                    trade_pnl = sell_sol - buy_sol
                    pnl_sol += trade_pnl
                    all_trades += 1

                    if trade_pnl > 0:
                        stats.winning_trades += 1
                    else:
                        stats.losing_trades += 1

                    # Fast trade check
                    duration = abs(sell_ts - buy_ts)
                    if 0 < duration < FAST_TRADE_THRESHOLD_SEC:
                        fast_trade_count += 1

        stats.total_trades = all_trades + max(0, len(all_mints) - len(sells))
        if stats.total_trades > 0:
            stats.win_rate = (stats.winning_trades / all_trades * 100) if all_trades else 0
            stats.fast_trades_pct = (fast_trade_count / stats.total_trades) * 100
            stats.smtb_pct = (smtb_count / stats.total_trades) * 100

        if total_bought_sol > 0:
            stats.roi = (pnl_sol / total_bought_sol) * 100
        stats.total_pnl_usd = pnl_sol  # in SOL; multiply by price for USD

        if timestamps:
            stats.first_trade_ts = min(timestamps)
            stats.last_trade_ts = max(timestamps)
            span_weeks = max(
                (stats.last_trade_ts - stats.first_trade_ts) / (7 * 86400), 1 / 7
            )
            stats.trades_per_week = stats.total_trades / span_weeks

        return stats

    # ------------------------------------------------------------------
    # Minimal RPC fallback (no API keys)
    # ------------------------------------------------------------------

    async def _fill_from_rpc(self, wallet: str, stats: WalletStats) -> WalletStats:
        sigs = await self.rpc.get_signatures_for_address(wallet, limit=100)
        stats.total_trades = len(sigs)
        if sigs:
            timestamps = [s.get("blockTime", 0) or 0 for s in sigs if s.get("blockTime")]
            if timestamps:
                stats.first_trade_ts = min(timestamps)
                stats.last_trade_ts = max(timestamps)
                span_weeks = max(
                    (stats.last_trade_ts - stats.first_trade_ts) / (7 * 86400), 1 / 7
                )
                stats.trades_per_week = len(timestamps) / span_weeks
        logger.warning(
            "No API keys set — ROI/WR/FastTrades/SMTB not available for %s", wallet
        )
        return stats

    # ------------------------------------------------------------------
    # SOL balance
    # ------------------------------------------------------------------

    async def _get_sol_balance(self, wallet: str) -> float:
        result = await self.rpc._rpc("getBalance", [wallet, {"commitment": "confirmed"}])
        if result and "value" in result:
            return result["value"] / 1e9
        return 0.0

    # ------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------

    def _compute_score(self, stats: WalletStats) -> float:
        """
        Composite smart-wallet score 0–100.
        Weights mirror Froggy v2 priority: WR > ROI > balance > frequency > tokens.
        """
        if stats.total_trades < settings.MIN_TOTAL_TRADES:
            return 0.0

        wr_score    = min(stats.win_rate, 100) * 0.30
        roi_score   = min(max(stats.roi, 0), 500) / 500 * 100 * 0.30
        bal_score   = min(stats.sol_balance / 10, 1) * 100 * 0.15
        freq_score  = min(stats.trades_per_week / 7, 1) * 100 * 0.15
        tok_score   = min(stats.tokens_total / 20, 1) * 100 * 0.10

        # Penalties
        fast_penalty = max(0, stats.fast_trades_pct - 15) * 2
        smtb_penalty = max(0, stats.smtb_pct - 10) * 2

        raw = wr_score + roi_score + bal_score + freq_score + tok_score
        return max(0.0, raw - fast_penalty - smtb_penalty)
