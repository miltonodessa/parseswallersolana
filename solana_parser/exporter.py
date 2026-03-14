"""Export parsed results to CSV, TXT, and Excel files."""
import csv
import logging
import os
from datetime import datetime

import settings
from .wallet_analyzer import WalletStats
from .cross_traders import CrossTrader

logger = logging.getLogger(__name__)


class Exporter:
    def __init__(self, results_dir: str = None):
        self.results_dir = results_dir or settings.RESULTS_DIR
        os.makedirs(self.results_dir, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _path(self, filename: str) -> str:
        return os.path.join(self.results_dir, filename)

    # ------------------------------------------------------------------
    # CSV / TXT
    # ------------------------------------------------------------------

    def export_wallets_csv(self, stats_list: list[WalletStats], filename: str = "") -> str:
        if not filename:
            filename = f"wallets_{self._timestamp()}.csv"
        path = self._path(filename)

        fieldnames = [
            "wallet", "total_trades", "winning_trades", "losing_trades",
            "win_rate_%", "roi_%", "total_pnl_usd", "avg_trade_size_sol",
            "fast_trades_%", "smtb_%", "sol_balance", "tokens_total",
            "trades_per_week", "score",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in stats_list:
                writer.writerow(s.to_dict())

        logger.info("Exported %d wallets (CSV) → %s", len(stats_list), path)
        return path

    def export_wallets_txt(self, stats_list: list[WalletStats], filename: str = "") -> str:
        """Plain list of wallet addresses, one per line."""
        if not filename:
            filename = f"wallets_{self._timestamp()}.txt"
        path = self._path(filename)

        with open(path, "w", encoding="utf-8") as f:
            for s in stats_list:
                f.write(s.wallet + "\n")

        logger.info("Exported %d wallet addresses (TXT) → %s", len(stats_list), path)
        return path

    def export_wallets_excel(self, stats_list: list[WalletStats], filename: str = "") -> str:
        """Export to .xlsx with Summary sheet + per-wallet sheets."""
        from .excel_exporter import export_wallets_excel as _do_export
        if not filename:
            filename = f"wallets_{self._timestamp()}.xlsx"
        return _do_export(stats_list, results_dir=self.results_dir, filename=filename)

    # ------------------------------------------------------------------
    # Cross-traders
    # ------------------------------------------------------------------

    def export_cross_traders_csv(self, traders: list[CrossTrader], filename: str = "") -> str:
        if not filename:
            filename = f"cross_traders_{self._timestamp()}.csv"
        path = self._path(filename)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["wallet", "token_count", "tokens"])
            writer.writeheader()
            for t in traders:
                writer.writerow({
                    "wallet": t.wallet,
                    "token_count": t.token_count,
                    "tokens": ",".join(t.tokens),
                })

        logger.info("Exported %d cross-traders (CSV) → %s", len(traders), path)
        return path

    # ------------------------------------------------------------------
    # Dev wallets
    # ------------------------------------------------------------------

    def export_dev_wallets_csv(self, dev_data: list[dict], filename: str = "") -> str:
        if not filename:
            filename = f"dev_wallets_{self._timestamp()}.csv"
        path = self._path(filename)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["mint", "dev_wallet", "name", "symbol"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(dev_data)

        logger.info("Exported %d dev wallets (CSV) → %s", len(dev_data), path)
        return path
