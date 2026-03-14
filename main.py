#!/usr/bin/env python3
"""
Solana Wallet Parser — entry point.

Usage:
  # Run Telegram bot
  python main.py bot

  # CLI: parse token holders
  python main.py parse_token <MINT>

  # CLI: find cross-traders
  python main.py cross_traders <MINT1> <MINT2> [MINT3 ...]

  # CLI: export dev wallets
  python main.py dev_wallets <MINT1> [MINT2 ...]
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _print_help():
    print(__doc__)


async def cli_parse_token(mint: str, filters: dict):
    from solana_parser import SolanaRPCClient, TokenParser, WalletAnalyzer, Exporter

    rpc = SolanaRPCClient()
    try:
        token_parser = TokenParser(rpc)
        analyzer = WalletAnalyzer(rpc)
        exporter = Exporter()

        print(f"[*] Parsing token: {mint}")
        token_info = await token_parser.parse_token(mint)
        print(f"[*] Found {len(token_info.holders)} holders")

        if token_info.dev_wallet:
            print(f"[*] Dev wallet: {token_info.dev_wallet}")

        wallets = [h.wallet for h in token_info.holders if h.wallet]

        print("[*] Analyzing wallets...")
        all_stats = await analyzer.analyze_wallets_batch(wallets, concurrency=5)

        filtered = [
            s for s in all_stats
            if analyzer.passes_filter(s, **filters)
        ]
        filtered.sort(key=lambda x: x.score, reverse=True)

        print(f"[*] Smart wallets after filter: {len(filtered)} / {len(all_stats)}")

        if filtered:
            csv_path = exporter.export_wallets_csv(filtered)
            txt_path = exporter.export_wallets_txt(filtered)
            print(f"[+] CSV: {csv_path}")
            print(f"[+] TXT: {txt_path}")

            print("\nTop 10 smart wallets:")
            for s in filtered[:10]:
                print(
                    f"  {s.wallet}  ROI={s.roi:.1f}%  WR={s.win_rate:.1f}%  "
                    f"PnL={s.total_pnl_usd:.2f}  trades={s.total_trades}  score={s.score:.1f}"
                )
    finally:
        await rpc.close()


async def cli_cross_traders(mints: list[str], min_token_count: int = 2):
    from solana_parser import SolanaRPCClient, CrossTraderFinder, Exporter

    rpc = SolanaRPCClient()
    try:
        finder = CrossTraderFinder(rpc)
        exporter = Exporter()

        print(f"[*] Searching cross-traders across {len(mints)} tokens...")
        traders = await finder.find_cross_traders(mints, min_token_count=min_token_count)

        print(f"[+] Found {len(traders)} cross-traders")
        if traders:
            path = exporter.export_cross_traders_csv(traders)
            print(f"[+] Exported to: {path}")
            for t in traders[:10]:
                print(f"  {t.wallet}  tokens={t.token_count}")
    finally:
        await rpc.close()


async def cli_dev_wallets(mints: list[str]):
    from solana_parser import SolanaRPCClient, TokenParser, Exporter

    rpc = SolanaRPCClient()
    try:
        parser = TokenParser(rpc)
        exporter = Exporter()

        dev_data = []
        for mint in mints:
            print(f"[*] Getting dev wallet for {mint[:8]}...")
            token_info = await parser.get_token_info(mint)
            dev_wallet = await parser.get_dev_wallet(mint)
            entry = {
                "mint": mint,
                "dev_wallet": dev_wallet or "not found",
                "name": token_info.name,
                "symbol": token_info.symbol,
            }
            dev_data.append(entry)
            print(f"    dev: {dev_wallet or 'not found'}  ({token_info.symbol})")

        path = exporter.export_dev_wallets_csv(dev_data)
        print(f"[+] Exported to: {path}")
    finally:
        await rpc.close()


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("help", "--help", "-h"):
        _print_help()
        return

    cmd = args[0]

    from config import MIN_ROI_FILTER, MIN_WINRATE_FILTER, MIN_TRADES_FILTER
    default_filters = {
        "min_roi": MIN_ROI_FILTER,
        "min_winrate": MIN_WINRATE_FILTER,
        "min_trades": MIN_TRADES_FILTER,
    }

    if cmd == "bot":
        from bot import run_bot
        run_bot()

    elif cmd == "parse_token":
        if len(args) < 2:
            print("Usage: python main.py parse_token <MINT>")
            sys.exit(1)
        asyncio.run(cli_parse_token(args[1], default_filters))

    elif cmd == "cross_traders":
        if len(args) < 3:
            print("Usage: python main.py cross_traders <MINT1> <MINT2> [MINT3 ...]")
            sys.exit(1)
        asyncio.run(cli_cross_traders(args[1:]))

    elif cmd == "dev_wallets":
        if len(args) < 2:
            print("Usage: python main.py dev_wallets <MINT1> [MINT2 ...]")
            sys.exit(1)
        asyncio.run(cli_dev_wallets(args[1:]))

    else:
        print(f"Unknown command: {cmd}")
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
