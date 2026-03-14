#!/usr/bin/env python3
"""
Solana Smart Wallet Parser  (Froggy v2 filters)
================================================

Commands
--------
  parse_token   <MINT> [options]     Parse holders of a token, analyze & filter wallets
  cross_traders <MINT> <MINT> ...    Find wallets that traded multiple tokens
  dev_wallets   <MINT> [MINT] ...    Export deployer wallets for tokens

Filter options (for parse_token)
---------------------------------
  --roi         MIN_ROI_%            default: 50
  --wr          MIN_WINRATE_%        default: 50
  --fast        MAX_FAST_TRADES_%    default: 15
  --smtb        MAX_SMTB_%           default: 10
  --balance     MIN_SOL_BALANCE      default: 2
  --tokens      MIN_TOKENS_TOTAL     default: 3
  --freq        MIN_TRADES_PER_WEEK  default: 1
  --trades      MIN_TOTAL_TRADES     default: 5

Other options
-------------
  --top N        Show top N results in console (default: 20)
  --no-csv       Skip CSV export
  --no-txt       Skip TXT export (address list)
  --debug        Verbose logging

Examples
--------
  python main.py parse_token EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  python main.py parse_token EPjFWdd5... --roi 100 --wr 60 --balance 5
  python main.py cross_traders MINT1 MINT2 MINT3
  python main.py dev_wallets MINT1 MINT2
"""

import argparse
import asyncio
import logging
import os
import sys

from config import (
    FILTER_MIN_ROI, FILTER_MIN_WINRATE, FILTER_MAX_FAST_TRADES_PCT,
    FILTER_MAX_SMTB_PCT, FILTER_MIN_BALANCE_SOL, FILTER_MIN_TOKENS_TOTAL,
    FILTER_MIN_TRADES_PER_WEEK, FILTER_MIN_TOTAL_TRADES,
)
from solana_parser import (
    SolanaRPCClient, TokenParser, WalletAnalyzer, WalletFilters,
    CrossTraderFinder, Exporter,
)


# ---------------------------------------------------------------------------
# parse_token
# ---------------------------------------------------------------------------

async def run_parse_token(mint: str, filters: WalletFilters, top: int, no_csv: bool, no_txt: bool):
    rpc = SolanaRPCClient()
    try:
        parser   = TokenParser(rpc)
        analyzer = WalletAnalyzer(rpc)
        exporter = Exporter()

        print(f"\n[*] Token : {mint}")
        print(f"[*] Filters: {filters.describe()}\n")

        # --- Step 1: parse holders ---
        print("[1/3] Fetching holders...")
        token_info = await parser.parse_token(mint)
        wallets = [h.wallet for h in token_info.holders if h.wallet]
        print(f"      {len(wallets)} holders found")

        if token_info.dev_wallet:
            print(f"      Dev wallet: {token_info.dev_wallet}")

        if not wallets:
            print("[-] No wallets to analyze.")
            return

        # --- Step 2: analyze ---
        print(f"\n[2/3] Analyzing {len(wallets)} wallets...")
        done_ref = [0]

        def sync_progress(done, total):
            done_ref[0] = done
            bar = _progress_bar(done, total)
            print(f"\r      {bar}  {done}/{total}", end="", flush=True)

        async def progress(done, total):
            sync_progress(done, total)

        all_stats = await analyzer.analyze_wallets_batch(
            wallets, concurrency=5, progress_callback=progress
        )
        print()  # newline after progress bar

        # --- Step 3: filter ---
        filtered = [s for s in all_stats if analyzer.passes_filter(s, filters)]
        filtered.sort(key=lambda x: x.score, reverse=True)

        print(f"\n[3/3] Results: {len(filtered)} smart wallets out of {len(all_stats)} analyzed")

        if not filtered:
            print("\n[!] No wallets passed the filters.")
            print("    Try lowering thresholds, e.g.: --roi 30 --wr 30 --balance 0")
            return

        # --- Print table ---
        _print_table(filtered[:top])

        # --- Export ---
        prefix = f"token_{mint[:8]}"
        if not no_csv:
            path = exporter.export_wallets_csv(filtered, f"{prefix}_{len(filtered)}w.csv")
            print(f"\n[+] CSV : {path}")
        if not no_txt:
            path = exporter.export_wallets_txt(filtered, f"{prefix}_{len(filtered)}w.txt")
            print(f"[+] TXT : {path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# cross_traders
# ---------------------------------------------------------------------------

async def run_cross_traders(mints: list[str], min_count: int = 2, no_csv: bool = False):
    rpc = SolanaRPCClient()
    try:
        finder   = CrossTraderFinder(rpc)
        exporter = Exporter()

        print(f"\n[*] Cross-trader search across {len(mints)} tokens")
        print(f"[*] Min token count: {min_count}\n")

        traders = await finder.find_cross_traders(mints, min_token_count=min_count)

        if not traders:
            print("[-] No cross-traders found.")
            return

        print(f"[+] Found {len(traders)} cross-traders\n")
        print(f"{'Wallet':<46} {'Tokens':>6}")
        print("-" * 55)
        for t in traders[:50]:
            print(f"  {t.wallet:<46} {t.token_count:>5}")

        if not no_csv:
            path = exporter.export_cross_traders_csv(traders)
            print(f"\n[+] CSV : {path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# dev_wallets
# ---------------------------------------------------------------------------

async def run_dev_wallets(mints: list[str], no_csv: bool = False):
    rpc = SolanaRPCClient()
    try:
        parser   = TokenParser(rpc)
        exporter = Exporter()

        print(f"\n[*] Fetching dev wallets for {len(mints)} token(s)\n")

        dev_data = []
        for mint in mints:
            info = await parser.get_token_info(mint)
            dev  = await parser.get_dev_wallet(mint)
            entry = {
                "mint":       mint,
                "dev_wallet": dev or "not found",
                "name":       info.name,
                "symbol":     info.symbol,
            }
            dev_data.append(entry)
            print(f"  {mint[:8]}...  dev: {(dev or 'not found')[:44]}  {info.symbol}")

        if not no_csv:
            path = exporter.export_dev_wallets_csv(dev_data)
            print(f"\n[+] CSV : {path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _progress_bar(done: int, total: int, width: int = 30) -> str:
    pct = done / total if total else 0
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:5.1%}"


def _print_table(stats_list):
    header = (
        f"  {'Wallet':<46} {'ROI%':>7} {'WR%':>6} {'Fast%':>6} "
        f"{'SMTB%':>6} {'SOL':>6} {'Tokens':>7} {'Freq/w':>7} {'Score':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in stats_list:
        print(
            f"  {s.wallet:<46} "
            f"{s.roi:>6.1f}% "
            f"{s.win_rate:>5.1f}% "
            f"{s.fast_trades_pct:>5.1f}% "
            f"{s.smtb_pct:>5.1f}% "
            f"{s.sol_balance:>6.2f} "
            f"{s.tokens_total:>7} "
            f"{s.trades_per_week:>6.1f} "
            f"{s.score:>6.1f}"
        )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Solana smart wallet parser (Froggy v2 filters)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command")

    # ── parse_token ──────────────────────────────────────────────────────
    pt = sub.add_parser("parse_token", help="Parse & filter wallets for a token")
    pt.add_argument("mint", help="Token mint address")
    pt.add_argument("--roi",     type=float, default=FILTER_MIN_ROI,              metavar="N", help="Min ROI %%")
    pt.add_argument("--wr",      type=float, default=FILTER_MIN_WINRATE,           metavar="N", help="Min WinRate %%")
    pt.add_argument("--fast",    type=float, default=FILTER_MAX_FAST_TRADES_PCT,   metavar="N", help="Max Fast Trades %%")
    pt.add_argument("--smtb",    type=float, default=FILTER_MAX_SMTB_PCT,          metavar="N", help="Max SMTB %%")
    pt.add_argument("--balance", type=float, default=FILTER_MIN_BALANCE_SOL,       metavar="N", help="Min SOL balance")
    pt.add_argument("--tokens",  type=int,   default=FILTER_MIN_TOKENS_TOTAL,      metavar="N", help="Min tokens traded")
    pt.add_argument("--freq",    type=float, default=FILTER_MIN_TRADES_PER_WEEK,   metavar="N", help="Min trades/week")
    pt.add_argument("--trades",  type=int,   default=FILTER_MIN_TOTAL_TRADES,      metavar="N", help="Min total trades")
    pt.add_argument("--top",     type=int,   default=20, help="Rows to print (default 20)")
    pt.add_argument("--no-csv",  action="store_true")
    pt.add_argument("--no-txt",  action="store_true")
    pt.add_argument("--debug",   action="store_true")

    # ── cross_traders ─────────────────────────────────────────────────────
    ct = sub.add_parser("cross_traders", help="Find wallets trading multiple tokens")
    ct.add_argument("mints", nargs="+", help="Two or more mint addresses")
    ct.add_argument("--min-count", type=int, default=2, help="Min tokens per wallet (default 2)")
    ct.add_argument("--no-csv", action="store_true")
    ct.add_argument("--debug",  action="store_true")

    # ── dev_wallets ───────────────────────────────────────────────────────
    dw = sub.add_parser("dev_wallets", help="Export deployer wallets for tokens")
    dw.add_argument("mints", nargs="+", help="One or more mint addresses")
    dw.add_argument("--no-csv", action="store_true")
    dw.add_argument("--debug",  action="store_true")

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    log_level = logging.DEBUG if getattr(args, "debug", False) else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs("results", exist_ok=True)

    if args.command == "parse_token":
        filters = WalletFilters(
            min_roi=args.roi,
            min_winrate=args.wr,
            max_fast_trades_pct=args.fast,
            max_smtb_pct=args.smtb,
            min_balance_sol=args.balance,
            min_tokens_total=args.tokens,
            min_trades_per_week=args.freq,
            min_total_trades=args.trades,
        )
        asyncio.run(run_parse_token(
            args.mint, filters, args.top, args.no_csv, args.no_txt
        ))

    elif args.command == "cross_traders":
        if len(args.mints) < 2:
            print("Error: provide at least 2 mint addresses")
            sys.exit(1)
        asyncio.run(run_cross_traders(args.mints, args.min_count, args.no_csv))

    elif args.command == "dev_wallets":
        asyncio.run(run_dev_wallets(args.mints, args.no_csv))


if __name__ == "__main__":
    main()
