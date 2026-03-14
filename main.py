#!/usr/bin/env python3
"""
Solana Smart Wallet Parser
==========================

Настройки и фильтры — в файле settings.py

Команды:
  parse_token   <MINT>               Парсить кошельки одного токена
  parse_tokens  <MINT> [MINT ...]    Парсить несколько токенов сразу (или --file)
  pump_today                         Загрузить токены pump.fun созданные сегодня
  cross_traders <MINT> <MINT> ...    Кошельки из нескольких токенов
  dev_wallets   <MINT> [MINT] ...    Dev-кошельки токенов

Опции:
  --debug   Подробный лог
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import settings
from solana_parser import (
    SolanaRPCClient, TokenParser, WalletAnalyzer, WalletFilters,
    CrossTraderFinder, Exporter,
)
from solana_parser.pump_fetcher import fetch_today_tokens, PumpToken, fetch_sol_price, migration_threshold_usd


# ---------------------------------------------------------------------------
# Общий pipeline: wallets → analyze → filter → export
# ---------------------------------------------------------------------------

async def _analyze_and_export(
    wallets: list[str],
    rpc: SolanaRPCClient,
    prefix: str,
    label: str = "",
):
    """Анализирует список кошельков, применяет фильтры, сохраняет результаты."""
    analyzer = WalletAnalyzer(rpc)
    exporter = Exporter()
    filters  = WalletFilters()

    if label:
        print(f"[*] Фильтры: {filters.describe()}\n")

    total = len(wallets)
    print(f"\n[2/3] Анализирую {total} уникальных кошельков...")

    async def progress(done, total_):
        bar = _progress_bar(done, total_)
        print(f"\r      {bar}  {done}/{total_}", end="", flush=True)

    all_stats = await analyzer.analyze_wallets_batch(
        wallets,
        concurrency=settings.WALLET_CONCURRENCY,
        progress_callback=progress,
    )
    print()

    filtered = [s for s in all_stats if analyzer.passes_filter(s, filters)]
    filtered.sort(key=lambda x: x.score, reverse=True)
    print(f"\n[3/3] Смарт-кошельков: {len(filtered)} из {len(all_stats)}")

    if not filtered:
        print("\n[!] Ни один кошелёк не прошёл фильтры.")
        _print_filter_diagnostics(all_stats, filters)
        return

    _print_table(filtered[:settings.TOP_RESULTS])

    csv_path  = exporter.export_wallets_csv(filtered,   f"{prefix}_{len(filtered)}w.csv")
    txt_path  = exporter.export_wallets_txt(filtered,   f"{prefix}_{len(filtered)}w.txt")
    xlsx_path = exporter.export_wallets_excel(filtered, f"{prefix}_{len(filtered)}w.xlsx")
    print(f"\n[+] CSV  : {csv_path}")
    print(f"[+] TXT  : {txt_path}")
    print(f"[+] XLSX : {xlsx_path}")


# ---------------------------------------------------------------------------
# parse_token  (один токен)
# ---------------------------------------------------------------------------

async def run_parse_token(mint: str):
    rpc = SolanaRPCClient()
    try:
        parser  = TokenParser(rpc)
        filters = WalletFilters()

        print(f"\n[*] Токен  : {mint}")
        print(f"[*] Фильтры: {filters.describe()}\n")

        print("[1/3] Загружаю холдеров...")
        token_info = await parser.parse_token(mint)
        wallets = [h.wallet for h in token_info.holders if h.wallet]
        print(f"      Найдено: {len(wallets)} кошельков")
        if token_info.dev_wallet:
            print(f"      Dev: {token_info.dev_wallet}")

        if not wallets:
            print("[-] Нет кошельков для анализа.")
            return

        await _analyze_and_export(wallets, rpc, prefix=f"token_{mint[:8]}")
    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# parse_tokens  (много токенов сразу)
# ---------------------------------------------------------------------------

async def run_parse_tokens(mints: list[str]):
    rpc = SolanaRPCClient()
    try:
        parser  = TokenParser(rpc)
        filters = WalletFilters()

        print(f"\n[*] Токенов для парсинга: {len(mints)}")
        print(f"[*] Параллельность токенов: {settings.TOKEN_CONCURRENCY}")
        print(f"[*] Фильтры: {filters.describe()}\n")

        print(f"[1/3] Собираю холдеров по {len(mints)} токенам...")

        token_done = [0]

        async def token_progress(done, total, mint):
            token_done[0] = done
            bar = _progress_bar(done, total)
            print(f"\r      {bar}  {done}/{total}  [{mint[:8]}...]", end="", flush=True)

        wallets = await parser.collect_unique_wallets(
            mints,
            token_concurrency=settings.TOKEN_CONCURRENCY,
            progress_callback=token_progress,
        )
        print(f"\n      Уникальных кошельков: {len(wallets)}")

        if not wallets:
            print("[-] Нет кошельков для анализа.")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        await _analyze_and_export(wallets, rpc, prefix=f"tokens_{len(mints)}t_{ts}")
    finally:
        await rpc.close()


# ---------------------------------------------------------------------------
# pump_today  (токены pump.fun созданные сегодня)
# ---------------------------------------------------------------------------

async def run_pump_today(
    analyze: bool = False,
    min_usd_mc: float = 0.0,
    only_migrated: bool = True,
    max_tokens: int = None,
):
    max_tokens = max_tokens or settings.PUMP_MAX_TOKENS_TODAY
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch SOL price and show migration threshold
    sol_price = await fetch_sol_price()
    migration_usd = migration_threshold_usd(sol_price) if sol_price else 0.0

    mode_label = (
        f"мигрированные сегодня (last_trade_timestamp ≥ {today_str})"
        if only_migrated else
        f"все созданные сегодня (created_timestamp ≥ {today_str})"
    )
    print(f"\n[*] Загружаю токены pump.fun — {mode_label} UTC...")
    print(f"[*] Режим: {'только мигрированные' if only_migrated else 'все токены (включая не мигрированные)'}")
    if sol_price:
        print(f"[*] SOL цена   : ${sol_price:,.2f}")
        print(f"[*] Порог миграции: ~${migration_usd:,.0f} USD  (85 SOL × ${sol_price:.2f} × 4.8)")
    if min_usd_mc > 0:
        print(f"[*] Фильтр: min USD Market Cap = ${min_usd_mc:,.0f}")
    print(f"[*] Макс. токенов: {max_tokens}\n")

    tokens = await fetch_today_tokens(
        max_tokens=max_tokens,
        min_usd_market_cap=min_usd_mc,
        only_graduated=only_migrated,
    )

    if not tokens:
        print("[-] Сегодня токенов не найдено (или API недоступен).")
        return

    print(f"[+] Найдено токенов сегодня: {len(tokens)}\n")
    _print_pump_table(tokens[:50])

    # Сохранить список токенов в CSV
    exporter = Exporter()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = _export_pump_tokens_csv(tokens, exporter, f"pump_today_{ts}.csv")
    print(f"\n[+] CSV токенов: {csv_path}")

    if not analyze:
        print("\n[i] Для анализа кошельков добавь флаг --analyze")
        print("    python main.py pump_today --analyze")
        return

    # Анализировать холдеров всех найденных токенов
    mints = [t.mint for t in tokens]
    print(f"\n[*] Запускаю анализ кошельков по {len(mints)} токенам...")
    rpc = SolanaRPCClient()
    try:
        parser = TokenParser(rpc)
        filters = WalletFilters()
        print(f"[*] Фильтры: {filters.describe()}\n")

        print(f"[1/3] Собираю холдеров по {len(mints)} токенам...")

        async def token_progress(done, total, mint):
            bar = _progress_bar(done, total)
            print(f"\r      {bar}  {done}/{total}  [{mint[:8]}...]", end="", flush=True)

        wallets = await parser.collect_unique_wallets(
            mints,
            token_concurrency=settings.TOKEN_CONCURRENCY,
            progress_callback=token_progress,
        )
        print(f"\n      Уникальных кошельков: {len(wallets)}")

        if not wallets:
            print("[-] Нет кошельков для анализа.")
            return

        await _analyze_and_export(
            wallets, rpc,
            prefix=f"pump_today_{today_str.replace('-', '')}",
        )
    finally:
        await rpc.close()


def _print_pump_table(tokens: list[PumpToken]):
    print(f"  {'#':>4}  {'Symbol':<10} {'Name':<28} {'USD MC':>12}  {'Grad':>5}  {'Mint':<44}")
    print("  " + "-" * 110)
    for i, t in enumerate(tokens, 1):
        grad = "YES" if t.complete else "-"
        name = t.name[:26] if t.name else "-"
        sym  = t.symbol[:8] if t.symbol else "-"
        print(f"  {i:>4}  {sym:<10} {name:<28} ${t.usd_market_cap:>11,.0f}  {grad:>5}  {t.mint}")


def _export_pump_tokens_csv(tokens: list[PumpToken], exporter: Exporter, filename: str) -> str:
    import csv
    path = exporter._path(filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "mint", "symbol", "name", "created_utc",
            "usd_market_cap", "market_cap_sol", "graduated",
        ])
        writer.writeheader()
        for t in tokens:
            writer.writerow({
                "mint":           t.mint,
                "symbol":         t.symbol,
                "name":           t.name,
                "created_utc":    t.created_dt().strftime("%Y-%m-%d %H:%M:%S"),
                "usd_market_cap": round(t.usd_market_cap, 2),
                "market_cap_sol": round(t.market_cap, 4),
                "graduated":      "yes" if t.complete else "no",
            })
    return path


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
    if not stats_list:
        return
    total = len(stats_list)
    keys = ["roi", "win_rate", "fast_trades", "smtb", "balance", "tokens_total",
            "trades_per_week", "total_trades"]
    labels = {
        "roi":             f"ROI ≥ {filters.min_roi}%",
        "win_rate":        f"WinRate ≥ {filters.min_winrate}%",
        "fast_trades":     f"FastTrades ≤ {filters.max_fast_trades_pct}%",
        "smtb":            f"SMTB ≤ {filters.max_smtb_pct}%",
        "balance":         f"Balance ≥ {filters.min_balance_sol} SOL",
        "tokens_total":    f"Tokens ≥ {filters.min_tokens_total}",
        "trades_per_week": f"Частота ≥ {filters.min_trades_per_week}/нед",
        "total_trades":    f"Сделок ≥ {filters.min_total_trades}",
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

    # parse_token — один токен
    pt = sub.add_parser("parse_token", help="Парсить кошельки одного токена")
    pt.add_argument("mint", help="Mint-адрес токена")

    # parse_tokens — много токенов
    pts = sub.add_parser("parse_tokens", help="Парсить несколько токенов сразу")
    pts_src = pts.add_mutually_exclusive_group(required=True)
    pts_src.add_argument("mints", nargs="*", default=[], metavar="MINT",
                         help="Mint-адреса через пробел")
    pts_src.add_argument("--file", metavar="PATH",
                         help="Файл со списком mint-адресов (по одному на строку)")

    # pump_today — токены pump.fun сегодня (по умолчанию — только мигрированные)
    pump = sub.add_parser("pump_today",
                          help="Мигрированные токены pump.fun созданные сегодня")
    pump.add_argument("--analyze", action="store_true",
                      help="Анализировать кошельки всех найденных токенов")
    pump.add_argument("--all", action="store_true", dest="all_tokens",
                      help="Включить НЕ мигрированные токены (по умолчанию только migrated)")
    pump.add_argument("--min-mc", type=float, default=0.0, metavar="USD",
                      help="Минимальный USD Market Cap (default: 0 = все)")
    pump.add_argument("--max", type=int, default=None, metavar="N",
                      help=f"Макс. кол-во токенов (default: {settings.PUMP_MAX_TOKENS_TODAY})")

    # cross_traders
    ct = sub.add_parser("cross_traders", help="Кросс-трейдеры нескольких токенов")
    ct.add_argument("mints", nargs="+", help="Два и более mint-адресов")
    ct.add_argument("--min-count", type=int, default=2, metavar="N",
                    help="Мин. кол-во токенов у кошелька (default: 2)")

    # dev_wallets
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

    elif args.command == "parse_tokens":
        mints = list(args.mints)
        if args.file:
            with open(args.file, encoding="utf-8") as f:
                mints = [line.strip() for line in f if line.strip()]
        if not mints:
            print("Ошибка: укажи mint-адреса или --file")
            sys.exit(1)
        print(f"[*] Загружено {len(mints)} mint-адресов")
        asyncio.run(run_parse_tokens(mints))

    elif args.command == "pump_today":
        asyncio.run(run_pump_today(
            analyze=args.analyze,
            min_usd_mc=args.min_mc,
            only_migrated=not args.all_tokens,
            max_tokens=args.max,
        ))

    elif args.command == "cross_traders":
        if len(args.mints) < 2:
            print("Ошибка: укажи минимум 2 mint-адреса")
            sys.exit(1)
        asyncio.run(run_cross_traders(args.mints, args.min_count))

    elif args.command == "dev_wallets":
        asyncio.run(run_dev_wallets(args.mints))


if __name__ == "__main__":
    main()
