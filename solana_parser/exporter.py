"""Export parsed results to CSV and text files."""
import csv
import logging
import os
from datetime import datetime
from typing import Union

from config import RESULTS_DIR
from .wallet_analyzer import WalletStats
from .cross_traders import CrossTrader

logger = logging.getLogger(__name__)


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


class Exporter:
    def __init__(self, results_dir: str = RESULTS_DIR):
        self.results_dir = results_dir
        _ensure_dir(results_dir)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def export_wallets_csv(
        self, stats_list: list[WalletStats], filename: str = ""
    ) -> str:
        if not filename:
            filename = f"wallets_{self._timestamp()}.csv"
        path = os.path.join(self.results_dir, filename)

        fieldnames = [
            "wallet", "total_trades", "winning_trades", "losing_trades",
            "win_rate", "total_pnl_usd", "roi", "avg_trade_size_usd",
            "tokens_traded", "sol_balance", "is_bot", "score",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in stats_list:
                writer.writerow(s.to_dict())

        logger.info("Exported %d wallets to %s", len(stats_list), path)
        return path

    def export_wallets_txt(
        self, stats_list: list[WalletStats], filename: str = ""
    ) -> str:
        """Plain list of wallet addresses (one per line)."""
        if not filename:
            filename = f"wallets_{self._timestamp()}.txt"
        path = os.path.join(self.results_dir, filename)

        with open(path, "w", encoding="utf-8") as f:
            for s in stats_list:
                f.write(s.wallet + "\n")

        logger.info("Exported %d wallet addresses to %s", len(stats_list), path)
        return path

    def export_cross_traders_csv(
        self, traders: list[CrossTrader], filename: str = ""
    ) -> str:
        if not filename:
            filename = f"cross_traders_{self._timestamp()}.csv"
        path = os.path.join(self.results_dir, filename)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["wallet", "token_count", "tokens"])
            writer.writeheader()
            for t in traders:
                writer.writerow({
                    "wallet": t.wallet,
                    "token_count": t.token_count,
                    "tokens": ",".join(t.tokens),
                })

        logger.info("Exported %d cross-traders to %s", len(traders), path)
        return path

    def export_dev_wallets_csv(
        self, dev_data: list[dict], filename: str = ""
    ) -> str:
        """Export dev wallets: [{mint, dev_wallet, name, symbol}]."""
        if not filename:
            filename = f"dev_wallets_{self._timestamp()}.csv"
        path = os.path.join(self.results_dir, filename)

        fieldnames = ["mint", "dev_wallet", "name", "symbol"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(dev_data)

        logger.info("Exported %d dev wallets to %s", len(dev_data), path)
        return path
