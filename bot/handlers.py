"""Telegram command handlers."""
import logging
import os

from telegram import Update, Document
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from solana_parser import SolanaRPCClient, TokenParser, WalletAnalyzer, CrossTraderFinder, Exporter
from config import MIN_ROI_FILTER, MIN_WINRATE_FILTER, MIN_TRADES_FILTER

logger = logging.getLogger(__name__)

# Shared instances (created once per process)
_rpc: SolanaRPCClient | None = None
_token_parser: TokenParser | None = None
_wallet_analyzer: WalletAnalyzer | None = None
_cross_finder: CrossTraderFinder | None = None
_exporter: Exporter | None = None


def _init_services():
    global _rpc, _token_parser, _wallet_analyzer, _cross_finder, _exporter
    if _rpc is None:
        _rpc = SolanaRPCClient()
        _token_parser = TokenParser(_rpc)
        _wallet_analyzer = WalletAnalyzer(_rpc)
        _cross_finder = CrossTraderFinder(_rpc)
        _exporter = Exporter()


# ---------------------------------------------------------------------------
# /start and /help
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🐸 *Solana Wallet Parser Bot*\n\n"
        "Команды:\n"
        "`/parse_token <MINT>` — парсить кошельки по контракту\n"
        "`/parse_cross_traders <MINT1> <MINT2> ...` — найти трейдеров нескольких токенов\n"
        "`/export_token_devs <MINT1> <MINT2> ...` — экспорт dev-кошельков\n"
        "`/set_filter roi=<N> wr=<N> trades=<N>` — настроить фильтры\n"
        "`/filters` — текущие фильтры\n"
        "`/help` — справка"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


# ---------------------------------------------------------------------------
# /parse_token
# ---------------------------------------------------------------------------

async def cmd_parse_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _init_services()
    args = ctx.args
    if not args:
        await update.message.reply_text("Использование: /parse_token <MINT_ADDRESS>")
        return

    mint = args[0].strip()
    filters = _get_filters(ctx)

    msg = await update.message.reply_text(f"⏳ Парсим токен `{mint[:8]}...`", parse_mode=ParseMode.MARKDOWN)

    try:
        token_info = await _token_parser.parse_token(mint)
        wallets = [h.wallet for h in token_info.holders if h.wallet]

        await msg.edit_text(
            f"✅ Найдено {len(wallets)} холдеров. Анализирую кошельки...",
        )

        done_count = [0]

        async def progress(done, total):
            done_count[0] = done
            if done % 20 == 0 or done == total:
                try:
                    await msg.edit_text(f"🔄 Анализ кошельков: {done}/{total}...")
                except Exception:
                    pass

        all_stats = await _wallet_analyzer.analyze_wallets_batch(
            wallets,
            concurrency=5,
            progress_callback=progress,
        )

        filtered = [s for s in all_stats if _wallet_analyzer.passes_filter(
            s, **filters
        )]
        filtered.sort(key=lambda x: x.score, reverse=True)

        if not filtered:
            await msg.edit_text(
                f"😔 После фильтрации (ROI≥{filters['min_roi']}%, WR≥{filters['min_winrate']}%) смарт-кошельков не найдено.\n"
                f"Всего проанализировано: {len(all_stats)}"
            )
            return

        csv_path = _exporter.export_wallets_csv(filtered, f"token_{mint[:8]}_{len(filtered)}wallets.csv")
        txt_path = _exporter.export_wallets_txt(filtered, f"token_{mint[:8]}_{len(filtered)}wallets.txt")

        summary = _build_wallets_summary(filtered[:5])
        await msg.edit_text(
            f"✅ Найдено *{len(filtered)}* смарт-кошельков из {len(all_stats)}\n\n"
            f"Топ-5:\n{summary}",
            parse_mode=ParseMode.MARKDOWN,
        )

        for path in [csv_path, txt_path]:
            with open(path, "rb") as f:
                await update.message.reply_document(document=f, filename=os.path.basename(path))

    except Exception as e:
        logger.exception("parse_token error")
        await msg.edit_text(f"❌ Ошибка: {e}")


# ---------------------------------------------------------------------------
# /parse_cross_traders
# ---------------------------------------------------------------------------

async def cmd_parse_cross_traders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _init_services()
    mints = [a.strip() for a in (ctx.args or []) if a.strip()]
    if len(mints) < 2:
        await update.message.reply_text(
            "Использование: /parse_cross_traders <MINT1> <MINT2> [MINT3 ...]"
        )
        return

    msg = await update.message.reply_text(f"⏳ Ищем кросс-трейдеров по {len(mints)} токенам...")

    try:
        traders = await _cross_finder.find_cross_traders(mints, min_token_count=2)

        if not traders:
            await msg.edit_text("Кросс-трейдеров не найдено.")
            return

        csv_path = _exporter.export_cross_traders_csv(traders)

        lines = [f"`{t.wallet}` — {t.token_count} токенов" for t in traders[:10]]
        await msg.edit_text(
            f"✅ Найдено *{len(traders)}* кросс-трейдеров\n\nТоп-10:\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
        )
        with open(csv_path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(csv_path))

    except Exception as e:
        logger.exception("parse_cross_traders error")
        await msg.edit_text(f"❌ Ошибка: {e}")


# ---------------------------------------------------------------------------
# /export_token_devs
# ---------------------------------------------------------------------------

async def cmd_export_token_devs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _init_services()
    mints = [a.strip() for a in (ctx.args or []) if a.strip()]
    if not mints:
        await update.message.reply_text("Использование: /export_token_devs <MINT1> [MINT2 ...]")
        return

    msg = await update.message.reply_text(f"⏳ Ищем dev-кошельки для {len(mints)} токенов...")

    try:
        dev_data = []
        for mint in mints:
            token_info = await _token_parser.get_token_info(mint)
            dev_wallet = await _token_parser.get_dev_wallet(mint)
            dev_data.append({
                "mint": mint,
                "dev_wallet": dev_wallet or "not found",
                "name": token_info.name,
                "symbol": token_info.symbol,
            })

        csv_path = _exporter.export_dev_wallets_csv(dev_data)

        lines = [
            f"`{d['mint'][:8]}...` — dev: `{(d['dev_wallet'] or 'N/A')[:8]}...` ({d['symbol']})"
            for d in dev_data
        ]
        await msg.edit_text(
            f"✅ Dev-кошельки ({len(dev_data)}):\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
        )
        with open(csv_path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(csv_path))

    except Exception as e:
        logger.exception("export_token_devs error")
        await msg.edit_text(f"❌ Ошибка: {e}")


# ---------------------------------------------------------------------------
# /set_filter and /filters
# ---------------------------------------------------------------------------

async def cmd_set_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_filter roi=100 wr=60 trades=10"""
    filters = _get_filters(ctx)
    changed = []
    for arg in (ctx.args or []):
        if "=" in arg:
            key, _, val = arg.partition("=")
            try:
                if key == "roi":
                    filters["min_roi"] = float(val)
                    changed.append(f"ROI ≥ {val}%")
                elif key == "wr":
                    filters["min_winrate"] = float(val)
                    changed.append(f"WinRate ≥ {val}%")
                elif key == "trades":
                    filters["min_trades"] = int(val)
                    changed.append(f"Trades ≥ {val}")
            except ValueError:
                pass
    ctx.user_data["filters"] = filters
    if changed:
        await update.message.reply_text("✅ Фильтры обновлены:\n" + "\n".join(changed))
    else:
        await update.message.reply_text("Использование: /set_filter roi=<N> wr=<N> trades=<N>")


async def cmd_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    f = _get_filters(ctx)
    await update.message.reply_text(
        f"Текущие фильтры:\n"
        f"• ROI ≥ {f['min_roi']}%\n"
        f"• WinRate ≥ {f['min_winrate']}%\n"
        f"• Trades ≥ {f['min_trades']}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_filters(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    defaults = {
        "min_roi": MIN_ROI_FILTER,
        "min_winrate": MIN_WINRATE_FILTER,
        "min_trades": MIN_TRADES_FILTER,
    }
    if ctx.user_data and "filters" in ctx.user_data:
        return ctx.user_data["filters"]
    return defaults


def _build_wallets_summary(stats_list) -> str:
    lines = []
    for s in stats_list:
        lines.append(
            f"• `{s.wallet[:8]}...` ROI={s.roi:.0f}% WR={s.win_rate:.0f}% "
            f"PnL={s.total_pnl_usd:.1f} trades={s.total_trades}"
        )
    return "\n".join(lines)
