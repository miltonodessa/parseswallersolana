#!/usr/bin/env python3
"""
Solana Smart Wallet Parser
==========================

Настройки и фильтры — в файле settings.py

Команды:
  parse_token   <MINT>             Парсить кошельки токена
  cross_traders <MINT> <MINT> ...  Кошельки из нескольких токенов
  dev_wallets   <MINT> [MINT] ...  Dev-кошельки токенов

Опции:
  --debug   Подробный лог
"""

import argparse
import asyncio
import logging
import os
import sys

import settings
from solana_parser import (
    SolanaRPCClient, TokenParser, WalletAnalyzer, WalletFilters,
    CrossTraderFinder, Exporter,
)


# ---------------------------------------------------------------------------
# parse_token
# ---------------------------------------------------------------------------

async def run_parse_token(mint: str):
    rpc = SolanaRPCClient()
    try:
        parser   = TokenParser(rpc)
        analyzer = WalletAnalyzer(rpc)
        exporter = Exporter()
        filters  = WalletFilters()   # читает все значения из settings.py

        print(f"\n[*] Токен  : {mint}")
        print(f"[*] Фильтры: {filters.describe()}\n")

        # 1. Холдеры
        print("[1/3] Загружаю холдеров...")
        token_info = await parser.parse_token(mint)
        wallets = [h.wallet for h in token_info.holders if h.wallet]
        print(f"      Найдено: {len(wallets)} кошельков")

        if token_info.dev_wallet:
            print(f"      Dev: {token_info.dev_wallet}")

        if not wallets:
            print("[-] Нет кошельков для анализа.")
            return

        # 2. Анализ
        print(f"\n[2/3] Анализирую {len(wallets)} кошельков...")

        async def progress(done, total):
            bar = _progress_bar(done, total)
            print(f"\r      {bar}  {done}/{total}", end="", flush=True)

        all_stats = await analyzer.analyze_wallets_batch(
            wallets, concurrency=5, progress_callback=progress
        )
        print()

        # 3. Фильтр
        filtered = [s for s in all_stats if analyzer.passes_filter(s, filters)]
        filtered.sort(key=lambda x: x.score, reverse=True)

        print(f"\n[3/3] Смарт-кошельков: {len(filtered)} из {len(all_stats)}")

        if not filtered:
            print("\n[!] Ни один кошелёк не прошёл фильтры.")
            _print_filter_diagnostics(all_stats, filters)
            return

        _print_table(filtered[:settings.TOP_RESULTS])

        prefix = f"token_{mint[:8]}"
        csv_path  = exporter.export_wallets_csv(filtered,   f"{prefix}_{len(filtered)}w.csv")
        txt_path  = exporter.export_wallets_txt(filtered,   f"{prefix}_{len(filtered)}w.txt")
        xlsx_path = exporter.export_wallets_excel(filtered, f"{prefix}_{len(filtered)}w.xlsx")
        print(f"\n[+] CSV  : {csv_path}")
        print(f"[+] TXT  : {txt_path}")
        print(f"[+] XLSX : {xlsx_path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# cross_traders
# ---------------------------------------------------------------------------

async def run_cross_traders(mints: list[str], min_count: int = 2):
    rpc = SolanaRPCClient()
    try:
        finder   = CrossTraderFinder(rpc)
        exporter = Exporter()

        print(f"\n[*] Кросс-трейдеры по {len(mints)} токенам (мин. {min_count} токена)\n")
        traders = await finder.find_cross_traders(mints, min_token_count=min_count)

        if not traders:
            print("[-] Кросс-трейдеров не найдено.")
            return

        print(f"[+] Найдено: {len(traders)}\n")
        print(f"  {'Кошелёк':<46} {'Токенов':>7}")
        print("  " + "-" * 55)
        for t in traders[:50]:
            print(f"  {t.wallet:<46} {t.token_count:>7}")

        path = exporter.export_cross_traders_csv(traders)
        print(f"\n[+] CSV : {path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# dev_wallets
# ---------------------------------------------------------------------------

async def run_dev_wallets(mints: list[str]):
    rpc = SolanaRPCClient()
    try:
        parser   = TokenParser(rpc)
        exporter = Exporter()

        print(f"\n[*] Dev-кошельки для {len(mints)} токена(ов)\n")
        dev_data = []
        for mint in mints:
            info = await parser.get_token_info(mint)
            dev  = await parser.get_dev_wallet(mint)
            dev_data.append({
                "mint":       mint,
                "dev_wallet": dev or "не найден",
                "name":       info.name,
                "symbol":     info.symbol,
            })
            print(f"  {mint[:8]}...  dev: {(dev or 'не найден')[:46]}  [{info.symbol}]")

        path = exporter.export_dev_wallets_csv(dev_data)
        print(f"\n[+] CSV : {path}")

    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _progress_bar(done: int, total: int, width: int = 30) -> str:
    pct = done / total if total else 0
    filled = int(pct * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:5.1%}"


def _print_filter_diagnostics(stats_list, filters):
    """Показать сводку — сколько кошельков провалило каждый фильтр."""
    if not stats_list:
        return
    total = len(stats_list)
    keys = ["roi", "win_rate", "fast_trades", "smtb", "balance", "tokens_total",
            "trades_per_week", "total_trades"]
    labels = {
        "roi":            f"ROI ≥ {filters.min_roi}%",
        "win_rate":       f"WinRate ≥ {filters.min_winrate}%",
        "fast_trades":    f"FastTrades ≤ {filters.max_fast_trades_pct}%",
        "smtb":           f"SMTB ≤ {filters.max_smtb_pct}%",
        "balance":        f"Balance ≥ {filters.min_balance_sol} SOL",
        "tokens_total":   f"Tokens ≥ {filters.min_tokens_total}",
        "trades_per_week":f"Частота ≥ {filters.min_trades_per_week}/нед",
        "total_trades":   f"Сделок ≥ {filters.min_total_trades}",
    }
    fail_counts = {k: 0 for k in keys}
    for s in stats_list:
        report = s.filter_report(filters)
        for k in keys:
            if not report[k]:
                fail_counts[k] += 1

    print("\n  Диагностика фильтров (сколько кошельков не прошли каждый фильтр):\n")
    for k in keys:
        fc = fail_counts[k]
        pct = fc / total * 100
        bar = "█" * int(pct / 5)
        print(f"  {labels[k]:<34}  {fc:>5}/{total}  {pct:>5.1f}%  {bar}")

    if fail_counts["roi"] == total and fail_counts["win_rate"] == total and fail_counts["balance"] == total:
        print("\n  ⚠  ВСЕ кошельки имеют ROI=0 / WinRate=0 / Balance=0.")
        print("     Это означает, что API ключи не заданы и данные не получены.")
        if not settings.HELIUS_API_KEY and not settings.BIRDEYE_API_KEY:
            print("\n  Решение:")
            print("  1. Зарегистрируйся на https://helius.dev  → бесплатный план")
            print("  2. Добавь в settings.py:")
            print('     HELIUS_API_KEY  = "твой_ключ"')
            print('     SOLANA_RPC_URL  = "https://mainnet.helius-rpc.com/?api-key=твой_ключ"')
            print("  3. Запусти снова — Helius даст историю свапов для расчёта ROI/WR")


def _print_table(stats_list):
    header = (
        f"  {'Кошелёк':<46} {'ROI%':>7} {'WR%':>6} {'Fast%':>6} "
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
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Solana Smart Wallet Parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--debug", action="store_true", help="Подробный лог")
    sub = p.add_subparsers(dest="command")

    pt = sub.add_parser("parse_token", help="Парсить кошельки токена")
    pt.add_argument("mint", help="Mint-адрес токена")

    ct = sub.add_parser("cross_traders", help="Кросс-трейдеры нескольких токенов")
    ct.add_argument("mints", nargs="+", help="Два и более mint-адресов")
    ct.add_argument("--min-count", type=int, default=2, metavar="N",
                    help="Мин. кол-во токенов у кошелька (default: 2)")

    dw = sub.add_parser("dev_wallets", help="Dev-кошельки токенов")
    dw.add_argument("mints", nargs="+", help="Один или более mint-адресов")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    logging.basicConfig(
        level=logging.DEBUG if (args.debug or settings.DEBUG) else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs(settings.RESULTS_DIR, exist_ok=True)

    if args.command == "parse_token":
        asyncio.run(run_parse_token(args.mint))

    elif args.command == "cross_traders":
        if len(args.mints) < 2:
            print("Ошибка: укажи минимум 2 mint-адреса")
            sys.exit(1)
        asyncio.run(run_cross_traders(args.mints, args.min_count))

    elif args.command == "dev_wallets":
        asyncio.run(run_dev_wallets(args.mints))


if __name__ == "__main__":
    main()
