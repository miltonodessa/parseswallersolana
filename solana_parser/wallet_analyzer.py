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

# Wrapped SOL mint — Jupiter often routes through wSOL instead of native SOL.
# Transactions using wSOL appear as tokenInputs/tokenOutputs, NOT nativeInput/nativeOutput.
_WSOL_MINT = "So11111111111111111111111111111111111111112"
FAST_TRADE_THRESHOLD_SEC = 180  # < 3 minutes = fast/suspicious trade


def _sol_from_swap_side(native: dict, tokens: list) -> float:
    """
    Extract total SOL lamports from one side of a Helius swap event.

    Native SOL and wSOL represent the *same* funds in Jupiter routes —
    Helius often emits both nativeInput/nativeOutput AND a wSOL entry in
    tokenInputs/tokenOutputs for the identical lamport amount.  Adding them
    together would double-count.  So we take whichever is larger.
    Returns SOL as a float (already converted from lamports).
    """
    native_lamps = float((native or {}).get("amount", 0) or 0)
    wsol_lamps = 0.0
    for tok in tokens:
        if tok.get("mint") == _WSOL_MINT:
            raw = tok.get("rawTokenAmount") or {}
            wsol_lamps += float(raw.get("tokenAmount", 0) or 0)
    # Use max to avoid double-counting when both represent the same flow
    return max(native_lamps, wsol_lamps) / 1e9


def _real_tokens(token_list: list) -> list:
    """Filter wSOL out of a token list — we want actual SPL tokens only."""
    return [t for t in token_list if t.get("mint") != _WSOL_MINT]


def _record_swap(swap: dict, ts: int,
                 buys: dict, sells: dict, tokens_traded: list) -> bool:
    """
    Parse one Helius swap object and update buys/sells dicts.

    Returns True if at least one buy or sell was recorded.

    Handles:
      - native SOL ↔ token  (standard pump.fun / Raydium swap)
      - wSOL ↔ token        (Jupiter routes via wrapped SOL)
    Token ↔ Token swaps (no SOL side) are ignored — can't compute SOL-PnL.
    """
    if not swap:
        return False

    native_in  = swap.get("nativeInput")  or {}
    native_out = swap.get("nativeOutput") or {}
    token_in   = swap.get("tokenInputs")  or []
    token_out  = swap.get("tokenOutputs") or []

    sol_in  = _sol_from_swap_side(native_in,  token_in)
    sol_out = _sol_from_swap_side(native_out, token_out)

    real_in  = _real_tokens(token_in)
    real_out = _real_tokens(token_out)

    recorded = False

    # ── Buy: spent SOL, received token(s) ─────────────────────────────
    if sol_in > 0 and real_out:
        # Attribute full SOL spent to each output token.
        # For single-token swaps (the common case) this is exact.
        # For rare multi-token output routes the cost is shared equally.
        per_token_sol = sol_in / len(real_out)
        for tok in real_out:
            mint = tok.get("mint", "")
            if mint:
                buys.setdefault(mint, []).append((per_token_sol, ts))
                if mint not in tokens_traded:
                    tokens_traded.append(mint)
                recorded = True

    # ── Sell: received SOL, sent token(s) ─────────────────────────────
    # Use `if` not `elif` — a transaction can legitimately have both
    # (e.g. a swap where SOL enters and different token exits, AND the
    # router also receives SOL on the output side from another leg).
    if sol_out > 0 and real_in:
        per_token_sol = sol_out / len(real_in)
        for tok in real_in:
            mint = tok.get("mint", "")
            if mint:
                sells.setdefault(mint, []).append((per_token_sol, ts))
                if mint not in tokens_traded:
                    tokens_traded.append(mint)
                recorded = True

    return recorded


@dataclass
class TokenTrade:
    """Per-token trading result inside a single wallet."""
    mint: str
    symbol: str = ""
    name: str = ""
    spent_sol: float = 0.0     # total SOL spent buying
    earned_sol: float = 0.0    # total SOL received from sells
    pnl_sol: float = 0.0       # earned - spent
    roi: float = 0.0            # (pnl / spent) * 100
    buys: int = 0
    sells: int = 0
    first_swap_ts: int = 0      # unix timestamp of first buy
    last_swap_ts: int = 0       # unix timestamp of last sell
    duration_sec: int = 0       # last_swap - first_swap


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
    closed_positions: int = 0        # tokens with both buy AND sell recorded

    # Internals
    tokens_traded: list[str] = field(default_factory=list)
    token_trades: list["TokenTrade"] = field(default_factory=list)
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
            "closed_positions": self.closed_positions >= filters.min_closed_trades,
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
    min_closed_trades: int = None   # min positions with both buy AND sell recorded

    def __post_init__(self):
        if self.min_roi is None:           self.min_roi = settings.MIN_ROI
        if self.min_winrate is None:       self.min_winrate = settings.MIN_WINRATE
        if self.max_fast_trades_pct is None: self.max_fast_trades_pct = settings.MAX_FAST_TRADES_PCT
        if self.max_smtb_pct is None:      self.max_smtb_pct = settings.MAX_SMTB_PCT
        if self.min_balance_sol is None:   self.min_balance_sol = settings.MIN_BALANCE_SOL
        if self.min_tokens_total is None:  self.min_tokens_total = settings.MIN_TOKENS_TOTAL
        if self.min_trades_per_week is None: self.min_trades_per_week = settings.MIN_TRADES_PER_WEEK
        if self.min_total_trades is None:  self.min_total_trades = settings.MIN_TOTAL_TRADES
        if self.min_closed_trades is None: self.min_closed_trades = getattr(settings, "MIN_CLOSED_TRADES", 5)

    def describe(self) -> str:
        return (
            f"ROI≥{self.min_roi}%  WR≥{self.min_winrate}%  "
            f"FastTrades≤{self.max_fast_trades_pct}%  SMTB≤{self.max_smtb_pct}%  "
            f"Balance≥{self.min_balance_sol}SOL  Tokens≥{self.min_tokens_total}  "
            f"Freq≥{self.min_trades_per_week}/wk  Trades≥{self.min_total_trades}  "
            f"ClosedPos≥{self.min_closed_trades}"
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

    async def prefilter_by_balance(
        self,
        wallets: list[str],
        min_sol: float,
        concurrency: int = 20,
        progress_callback=None,
    ) -> list[str]:
        """
        Fast pre-filter: drop wallets whose SOL balance < min_sol.

        Uses batched getMultipleAccounts (100 wallets per RPC call) so the
        cost is ~len(wallets)/100 RPC calls instead of len(wallets) calls.
        At public RPC limits this takes ~2-5 minutes for 273k wallets.

        Returns the subset of wallets that pass the balance check.
        """
        if min_sol <= 0:
            return wallets

        passing: list[str] = []
        batch_size = 100
        total_batches = (len(wallets) + batch_size - 1) // batch_size
        done = 0
        sem = asyncio.Semaphore(concurrency)

        async def _check_batch(batch: list[str]):
            nonlocal done
            async with sem:
                balances = await self.rpc.get_sol_balances_batch(batch, batch_size=len(batch))
                ok = [w for w in batch if balances.get(w, 0) >= min_sol]
                return ok

        batches = [wallets[i : i + batch_size] for i in range(0, len(wallets), batch_size)]
        tasks = [_check_batch(b) for b in batches]

        for coro in asyncio.as_completed(tasks):
            ok = await coro
            passing.extend(ok)
            done += 1
            if progress_callback:
                await progress_callback(done, total_batches, len(passing))

        return passing

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
        import time as _time
        since_ts = int(_time.time()) - settings.WALLET_ANALYSIS_DAYS * 86400
        txs = await self.rpc.helius_get_parsed_transactions(
            [wallet],
            tx_type="SWAP",
            since_ts=since_ts,
            max_pages=getattr(settings, "HELIUS_MAX_PAGES", 2),
        )
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
            swap = events.get("swap") or {}
            if not swap:
                continue

            # Try to record from the top-level swap event first.
            # If it yields nothing (e.g. complex Jupiter multi-hop where
            # the outer wrapper has no direct SOL flow), fall back to
            # innerSwaps which describe each individual leg of the route.
            recorded = _record_swap(swap, ts, buys, sells, stats.tokens_traded)
            if not recorded:
                for inner in swap.get("innerSwaps") or []:
                    _record_swap(inner, ts, buys, sells, stats.tokens_traded)

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
        closed_positions = 0   # tokens that have at least one sell
        total_positions = 0    # tokens that have at least one buy

        # Realized PnL: only for positions that have both buys and sells
        closed_buy_sol  = 0.0
        closed_sell_sol = 0.0

        all_mints = set(list(buys.keys()) + list(sells.keys()))

        for mint in all_mints:
            buy_list  = sorted(buys.get(mint, []),  key=lambda x: x[1])
            sell_list = sorted(sells.get(mint, []), key=lambda x: x[1])

            t_spent  = sum(b[0] for b in buy_list)
            t_earned = sum(s[0] for s in sell_list)

            # SMTB: sells with no matching buy (received via airdrop/transfer)
            if len(sell_list) > len(buy_list):
                smtb_count += len(sell_list) - len(buy_list)

            all_ts = [ts for _, ts in buy_list + sell_list if ts]
            first_ts = min(all_ts) if all_ts else 0
            last_ts  = max(all_ts) if all_ts else 0
            t_pnl = t_earned - t_spent
            t_roi = (t_pnl / t_spent * 100) if t_spent > 0 else 0.0

            if buy_list:
                total_positions += 1

                if sell_list:
                    # ── Closed position: has both buy and sell ──────────────────
                    closed_positions += 1
                    closed_buy_sol  += t_spent
                    closed_sell_sol += t_earned

                    if t_pnl > 0:
                        stats.winning_trades += 1
                    else:
                        stats.losing_trades += 1

                    # Fast trade: time from first buy to first sell
                    first_buy_ts  = buy_list[0][1]
                    first_sell_ts = sell_list[0][1]
                    duration = abs(first_sell_ts - first_buy_ts)
                    if 0 < duration < FAST_TRADE_THRESHOLD_SEC:
                        fast_trade_count += 1
                # else: open position (still held) — not counted in WR/fast

            stats.token_trades.append(TokenTrade(
                mint=mint,
                spent_sol=t_spent,
                earned_sol=t_earned,
                pnl_sol=t_pnl,
                roi=t_roi,
                buys=len(buy_list),
                sells=len(sell_list),
                first_swap_ts=first_ts,
                last_swap_ts=last_ts,
                duration_sec=abs(last_ts - first_ts),
            ))

        stats.token_trades.sort(key=lambda t: abs(t.pnl_sol), reverse=True)

        # total_trades = all unique token positions (bought or sold)
        stats.total_trades = total_positions
        stats.closed_positions = closed_positions

        # WR and fast-trade % over CLOSED positions only
        if closed_positions > 0:
            stats.win_rate        = (stats.winning_trades / closed_positions) * 100
            stats.fast_trades_pct = (fast_trade_count / closed_positions) * 100

        # SMTB % over total positions
        if total_positions > 0:
            stats.smtb_pct = (smtb_count / total_positions) * 100

        # ROI = realized PnL on closed positions / capital deployed in closed positions
        realized_pnl = closed_sell_sol - closed_buy_sol
        if closed_buy_sol > 0:
            stats.roi = (realized_pnl / closed_buy_sol) * 100
        stats.total_pnl_usd = realized_pnl  # in SOL units (no USD conversion without price)

        if timestamps:
            stats.first_trade_ts = min(timestamps)
            stats.last_trade_ts  = max(timestamps)
            span_weeks = max(
                (stats.last_trade_ts - stats.first_trade_ts) / (7 * 86400), 1 / 7
            )
            stats.trades_per_week = total_positions / span_weeks

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
