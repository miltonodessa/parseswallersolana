"""Telegram bot setup and runner."""
import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from config import TELEGRAM_BOT_TOKEN
from .handlers import (
    cmd_start,
    cmd_help,
    cmd_parse_token,
    cmd_parse_cross_traders,
    cmd_export_token_devs,
    cmd_set_filter,
    cmd_filters,
)

logger = logging.getLogger(__name__)


def run_bot():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("parse_token", cmd_parse_token))
    app.add_handler(CommandHandler("parse_cross_traders", cmd_parse_cross_traders))
    app.add_handler(CommandHandler("export_token_devs", cmd_export_token_devs))
    app.add_handler(CommandHandler("set_filter", cmd_set_filter))
    app.add_handler(CommandHandler("filters", cmd_filters))

    logger.info("Bot started. Polling...")
    app.run_polling()
