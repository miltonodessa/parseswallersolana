"""
Excel exporter — produces a .xlsx file matching the Froggy v2 spreadsheet structure.

Layout:
  Sheet "Summary"          — one row per wallet, all key metrics
  Sheet "Wallet_<addr8>"   — one sheet per wallet with:
      Block A (rows 1-2)   : column headers + wallet summary values
      Block B (rows 4-16)  : wallet stats + profit distribution table
      Block C (rows 18+)   : per-token trade breakdown table
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import (
    Alignment, Border, Font, GradientFill, PatternFill, Side,
)
from openpyxl.styles.numbers import FORMAT_PERCENTAGE_00
from openpyxl.utils import get_column_letter

from .wallet_analyzer import WalletStats, TokenTrade
import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
C_HEADER_BG   = "1F4E79"   # dark blue  — section headers
C_HEADER_FG   = "FFFFFF"   # white text
C_SUBHDR_BG   = "2E75B6"   # mid blue   — sub-headers
C_ALT_ROW     = "EBF3FB"   # light blue — alternating rows
C_GREEN_DARK  = "00B050"   # ROI > 500%
C_GREEN_MID   = "92D050"   # ROI 100-500%
C_GREEN_LIGHT = "C6EFCE"   # ROI 50-100%
C_YELLOW      = "FFEB9C"   # ROI 0-50%
C_RED_LIGHT   = "FFCCCC"   # ROI 0 to -50%
C_RED_DARK    = "FF0000"   # ROI < -50%
C_ORANGE      = "FF9900"   # fast-trade / smtb warning
C_NEUTRAL_BG  = "F2F2F2"   # light gray stats block


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _font(bold=False, color="000000", size=11) -> Font:
    return Font(bold=bold, color=color, size=size)


def _center() -> Alignment:
    return Alignment(horizontal="center", vertical="center", wrap_text=True)


def _left() -> Alignment:
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


def _thin_border() -> Border:
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)


def _roi_fill(roi: float) -> PatternFill:
    if roi > 500:   return _fill(C_GREEN_DARK)
    if roi > 100:   return _fill(C_GREEN_MID)
    if roi > 50:    return _fill(C_GREEN_LIGHT)
    if roi >= 0:    return _fill(C_YELLOW)
    if roi > -50:   return _fill(C_RED_LIGHT)
    return _fill(C_RED_DARK)


def _roi_font(roi: float) -> Font:
    if roi > 100 or roi < -50:
        return _font(bold=True, color="FFFFFF")
    return _font(bold=True, color="000000")


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _fmt_duration(sec: int) -> str:
    if sec <= 0:
        return "-"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if not parts: parts.append(f"{s}s")
    return " ".join(parts)


def _fmt_sol(v: float) -> str:
    return f"{v:.4f}" if v else "0"


def _apply_header_style(cell, bg=C_HEADER_BG):
    cell.fill  = _fill(bg)
    cell.font  = _font(bold=True, color=C_HEADER_FG, size=10)
    cell.alignment = _center()
    cell.border = _thin_border()


def _apply_label_style(cell):
    cell.fill  = _fill(C_NEUTRAL_BG)
    cell.font  = _font(bold=True, size=10)
    cell.alignment = _left()
    cell.border = _thin_border()


def _apply_value_style(cell, bold=False):
    cell.font  = _font(bold=bold, size=10)
    cell.alignment = _center()
    cell.border = _thin_border()


# ---------------------------------------------------------------------------
# Profit / Distribution bucket helpers
# ---------------------------------------------------------------------------

_BUCKETS = [
    (">500%",      lambda r: r > 500),
    ("500-100%",   lambda r: 100 < r <= 500),
    ("100-50%",    lambda r: 50  < r <= 100),
    ("50-0%",      lambda r: 0   < r <= 50),
    ("0%-50%",     lambda r: -50 < r <= 0),
    ("-50%-100%",  lambda r: r   <= -50),
]

_BUCKET_FILLS = [
    _fill(C_GREEN_DARK),
    _fill(C_GREEN_MID),
    _fill(C_GREEN_LIGHT),
    _fill(C_YELLOW),
    _fill(C_RED_LIGHT),
    _fill(C_RED_DARK),
]
_BUCKET_FONTS = [
    _font(bold=True, color="FFFFFF"),
    _font(bold=True, color="000000"),
    _font(bold=True, color="000000"),
    _font(bold=True, color="000000"),
    _font(bold=True, color="000000"),
    _font(bold=True, color="FFFFFF"),
]


def _bucket_stats(token_trades: list[TokenTrade]) -> list[dict]:
    results = []
    total = len(token_trades)
    for label, pred in _BUCKETS:
        matched = [t for t in token_trades if pred(t.roi)]
        count   = len(matched)
        pct     = (count / total * 100) if total else 0
        pnl     = sum(t.pnl_sol for t in matched)
        results.append({"label": label, "count": count, "pct": pct, "pnl": pnl})
    return results


# ===========================================================================
# Summary sheet
# ===========================================================================

_SUMMARY_COLS = [
    ("Wallet",           44),
    ("Balance SOL",      12),
    ("Winrate %",        11),
    ("PNL SOL",          11),
    ("ROI %",            10),
    ("Spent SOL",        11),
    ("Earned SOL",       11),
    ("Fast Trades %",    13),
    ("SMTB %",           10),
    ("Tokens",            8),
    ("Trades/wk",        10),
    ("Score",             8),
]


def _build_summary_sheet(ws, stats_list: list[WalletStats]):
    ws.title = "Summary"
    ws.freeze_panes = "A2"

    # Column widths
    for i, (_, w) in enumerate(_SUMMARY_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.row_dimensions[1].height = 30

    # Header row
    for col_idx, (label, _) in enumerate(_SUMMARY_COLS, 1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        _apply_header_style(cell)

    # Data rows
    for row_idx, s in enumerate(stats_list, 2):
        alt = (row_idx % 2 == 0)
        row_fill = _fill(C_ALT_ROW) if alt else _fill("FFFFFF")

        total_spent  = sum(t.spent_sol  for t in s.token_trades)
        total_earned = sum(t.earned_sol for t in s.token_trades)

        values = [
            s.wallet,
            round(s.sol_balance, 4),
            round(s.win_rate, 2),
            round(s.total_pnl_usd, 4),
            round(s.roi, 2),
            round(total_spent, 4),
            round(total_earned, 4),
            round(s.fast_trades_pct, 2),
            round(s.smtb_pct, 2),
            s.tokens_total,
            round(s.trades_per_week, 2),
            round(s.score, 2),
        ]

        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _thin_border()
            cell.alignment = _center() if col_idx > 1 else _left()

            # Colour ROI column
            if col_idx == 5:    # ROI %
                cell.fill = _roi_fill(s.roi)
                cell.font = _roi_font(s.roi)
            elif col_idx == 4:  # PNL SOL
                cell.fill = _fill(C_GREEN_LIGHT) if s.total_pnl_usd >= 0 else _fill(C_RED_LIGHT)
            elif col_idx in (8, 9):  # Fast Trades, SMTB — warn if high
                cell.fill = _fill(C_ORANGE) if (
                    (col_idx == 8 and s.fast_trades_pct > settings.MAX_FAST_TRADES_PCT) or
                    (col_idx == 9 and s.smtb_pct > settings.MAX_SMTB_PCT)
                ) else row_fill
            else:
                cell.fill = row_fill


# ===========================================================================
# Individual wallet sheet
# ===========================================================================

def _build_wallet_sheet(wb: Workbook, s: WalletStats):
    sheet_name = f"Wallet_{s.wallet[:8]}"
    ws = wb.create_sheet(title=sheet_name)
    ws.freeze_panes = "A3"

    total_spent  = sum(t.spent_sol  for t in s.token_trades)
    total_earned = sum(t.earned_sol for t in s.token_trades)
    avg_buys  = (sum(t.buys  for t in s.token_trades) / len(s.token_trades)) if s.token_trades else 0
    avg_sells = (sum(t.sells for t in s.token_trades) / len(s.token_trades)) if s.token_trades else 0

    # ---- Column widths ----
    col_widths = {
        "A": 46, "B": 16, "C": 16, "D": 12,
        "E": 12, "F": 12, "G": 12, "H": 14,
        "I": 16, "J": 22, "K": 22, "L": 10, "M": 10,
    }
    for col, w in col_widths.items():
        ws.column_dimensions[col].width = w

    # ==================================================================
    # BLOCK A — Row 1: column headers   Row 2: wallet summary values
    # ==================================================================

    hdr_labels = [
        "Wallet", "Balance SOL", "Winrate %", "PNL SOL", "ROI %",
        "Spent SOL", "Earned SOL", "Fast Trades %", "SMTB %",
        "Holding pos.", "Median Sell %", "Median Trade", "Score",
    ]
    for col_idx, label in enumerate(hdr_labels, 1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        _apply_header_style(cell)
    ws.row_dimensions[1].height = 28

    summary_vals = [
        s.wallet,
        round(s.sol_balance, 4),
        round(s.win_rate, 2),
        round(s.total_pnl_usd, 4),
        round(s.roi, 2),
        round(total_spent, 4),
        round(total_earned, 4),
        round(s.fast_trades_pct, 2),
        round(s.smtb_pct, 2),
        "-",                           # Holding positions — not available
        "-",                           # Median Sell %     — not available
        round(s.avg_trade_size_sol, 6),
        round(s.score, 2),
    ]
    for col_idx, val in enumerate(summary_vals, 1):
        cell = ws.cell(row=2, column=col_idx, value=val)
        cell.border    = _thin_border()
        cell.alignment = _center() if col_idx > 1 else _left()
        cell.font      = _font(bold=(col_idx == 1), size=10)

        if col_idx == 5:   # ROI
            cell.fill = _roi_fill(s.roi)
            cell.font = _roi_font(s.roi)
        elif col_idx == 4:  # PNL
            cell.fill = _fill(C_GREEN_LIGHT) if s.total_pnl_usd >= 0 else _fill(C_RED_LIGHT)
        elif col_idx == 8:  # Fast Trades
            cell.fill = _fill(C_ORANGE) if s.fast_trades_pct > settings.MAX_FAST_TRADES_PCT else _fill("FFFFFF")
        elif col_idx == 9:  # SMTB
            cell.fill = _fill(C_ORANGE) if s.smtb_pct > settings.MAX_SMTB_PCT else _fill("FFFFFF")
    ws.row_dimensions[2].height = 22

    # ==================================================================
    # BLOCK B — Rows 4-16: stats panel + profit distribution table
    # ==================================================================

    # Left stats panel (col A-B)
    stats_panel = [
        ("AVG Trade Duration",   _fmt_duration(
            int((s.last_trade_ts - s.first_trade_ts) / max(s.total_trades, 1))
            if s.first_trade_ts and s.last_trade_ts else 0)),
        ("AVG Buys",             round(avg_buys, 1)),
        ("AVG Sells",            round(avg_sells, 1)),
        ("AVG ROI %",            round(s.roi, 2)),
        ("Winrate %",            round(s.win_rate, 2)),
        ("SMTB SPL %",           round(s.smtb_pct, 2)),
        ("Fast Trades %",        round(s.fast_trades_pct, 2)),
        ("Balance SOL",          round(s.sol_balance, 4)),
        ("Tokens",               s.tokens_total),
        ("Trades/week",          round(s.trades_per_week, 2)),
        ("First Swap",           _fmt_ts(s.first_trade_ts)),
        ("Last Swap",            _fmt_ts(s.last_trade_ts)),
        ("Score",                round(s.score, 2)),
    ]
    for i, (label, value) in enumerate(stats_panel):
        row = 4 + i
        lc  = ws.cell(row=row, column=1, value=label)
        vc  = ws.cell(row=row, column=2, value=value)
        _apply_label_style(lc)
        _apply_value_style(vc)
        # Highlight warning values
        if label in ("SMTB SPL %",) and isinstance(value, float) and value > settings.MAX_SMTB_PCT:
            vc.fill = _fill(C_ORANGE)
        if label in ("Fast Trades %",) and isinstance(value, float) and value > settings.MAX_FAST_TRADES_PCT:
            vc.fill = _fill(C_ORANGE)

    # Profit / Distribution sub-header (col E, row 4)
    pd_title = ws.cell(row=4, column=5, value="Profit / Distribution")
    _apply_header_style(pd_title, bg=C_SUBHDR_BG)
    ws.merge_cells(start_row=4, start_column=5, end_row=4, end_column=10)

    # Distribution bucket headers (row 5, cols E-J)
    bucket_stats = _bucket_stats(s.token_trades)
    for bi, (bstat, bfill, bfont) in enumerate(zip(bucket_stats, _BUCKET_FILLS, _BUCKET_FONTS)):
        col = 5 + bi
        hc = ws.cell(row=5, column=col, value=bstat["label"])
        hc.fill  = bfill
        hc.font  = bfont
        hc.alignment = _center()
        hc.border    = _thin_border()

    # Row 6: Count
    ws.cell(row=6, column=4, value="Count").fill = _fill(C_NEUTRAL_BG)
    ws["D6"].font = _font(bold=True, size=10); ws["D6"].border = _thin_border(); ws["D6"].alignment = _center()
    for bi, bstat in enumerate(bucket_stats):
        cell = ws.cell(row=6, column=5 + bi, value=bstat["count"])
        _apply_value_style(cell)

    # Row 7: Percent %
    ws.cell(row=7, column=4, value="Percent %").fill = _fill(C_NEUTRAL_BG)
    ws["D7"].font = _font(bold=True, size=10); ws["D7"].border = _thin_border(); ws["D7"].alignment = _center()
    for bi, bstat in enumerate(bucket_stats):
        cell = ws.cell(row=7, column=5 + bi, value=round(bstat["pct"], 2))
        _apply_value_style(cell)

    # Row 8: PnL SOL
    ws.cell(row=8, column=4, value="PnL (SOL)").fill = _fill(C_NEUTRAL_BG)
    ws["D8"].font = _font(bold=True, size=10); ws["D8"].border = _thin_border(); ws["D8"].alignment = _center()
    for bi, bstat in enumerate(bucket_stats):
        cell = ws.cell(row=8, column=5 + bi, value=round(bstat["pnl"], 4))
        _apply_value_style(cell)
        cell.fill = _fill(C_GREEN_LIGHT) if bstat["pnl"] >= 0 else _fill(C_RED_LIGHT)

    # ==================================================================
    # BLOCK C — Token breakdown table
    # ==================================================================

    TOKEN_HDR_ROW = 12
    TOKEN_DATA_START = TOKEN_HDR_ROW + 1

    token_headers = [
        ("Token Name / Mint",  "A"),
        ("SPL Income",         "B"),
        ("SPL Outcome",        "C"),
        ("Spent SOL",          "D"),
        ("Earned SOL",         "E"),
        ("PNL SOL",            "F"),
        ("ROI %",              "G"),
        ("Duration",           "H"),
        ("Buys/Sells",         "I"),
        ("First Swap",         "J"),
        ("Last Swap",          "K"),
        ("GMGN",               "L"),
        ("Pool",               "M"),
    ]

    ws.row_dimensions[TOKEN_HDR_ROW].height = 28
    for col_idx, (label, _) in enumerate(token_headers, 1):
        cell = ws.cell(row=TOKEN_HDR_ROW, column=col_idx, value=label)
        _apply_header_style(cell)

    if not s.token_trades:
        ws.cell(row=TOKEN_DATA_START, column=1, value="Нет данных по токенам")
        return

    for row_offset, t in enumerate(s.token_trades):
        row = TOKEN_DATA_START + row_offset
        alt = (row_offset % 2 == 0)
        row_fill = _fill(C_ALT_ROW) if alt else _fill("FFFFFF")
        ws.row_dimensions[row].height = 18

        # A: mint (short) + symbol
        name_str = f"{t.symbol or t.mint[:12]}"
        ca = ws.cell(row=row, column=1, value=name_str)
        ca.fill = row_fill; ca.border = _thin_border(); ca.alignment = _left()

        # B: SPL Income (token units — shown as "—" since we track SOL)
        cb = ws.cell(row=row, column=2, value="-")
        cb.fill = row_fill; cb.border = _thin_border(); cb.alignment = _center()

        # C: SPL Outcome
        cc = ws.cell(row=row, column=3, value="-")
        cc.fill = row_fill; cc.border = _thin_border(); cc.alignment = _center()

        # D: Spent SOL
        cd = ws.cell(row=row, column=4, value=round(t.spent_sol, 4))
        cd.fill = row_fill; cd.border = _thin_border(); cd.alignment = _center()

        # E: Earned SOL
        ce = ws.cell(row=row, column=5, value=round(t.earned_sol, 4))
        ce.fill = row_fill; ce.border = _thin_border(); ce.alignment = _center()

        # F: PNL SOL — coloured
        cf = ws.cell(row=row, column=6, value=round(t.pnl_sol, 4))
        cf.fill = _fill(C_GREEN_LIGHT) if t.pnl_sol >= 0 else _fill(C_RED_LIGHT)
        cf.font = _font(bold=True)
        cf.border = _thin_border(); cf.alignment = _center()

        # G: ROI % — coloured
        cg = ws.cell(row=row, column=7, value=round(t.roi, 2))
        cg.fill = _roi_fill(t.roi)
        cg.font = _roi_font(t.roi)
        cg.border = _thin_border(); cg.alignment = _center()

        # H: Duration
        ch = ws.cell(row=row, column=8, value=_fmt_duration(t.duration_sec))
        ch.fill = row_fill; ch.border = _thin_border(); ch.alignment = _center()

        # I: Buys / Sells
        ci = ws.cell(row=row, column=9, value=f"{t.buys}/{t.sells}")
        ci.fill = row_fill; ci.border = _thin_border(); ci.alignment = _center()

        # J: First Swap
        cj = ws.cell(row=row, column=10, value=_fmt_ts(t.first_swap_ts))
        cj.fill = row_fill; cj.border = _thin_border(); cj.alignment = _center()

        # K: Last Swap
        ck = ws.cell(row=row, column=11, value=_fmt_ts(t.last_swap_ts))
        ck.fill = row_fill; ck.border = _thin_border(); ck.alignment = _center()

        # L: GMGN link
        gmgn_url = f"https://gmgn.ai/sol/token/{t.mint}"
        cl = ws.cell(row=row, column=12, value="Link")
        cl.hyperlink = gmgn_url
        cl.font = Font(color="0070C0", underline="single", size=10)
        cl.fill = row_fill; cl.border = _thin_border(); cl.alignment = _center()

        # M: Pool (Birdeye)
        birdeye_url = f"https://birdeye.so/token/{t.mint}?chain=solana"
        cm = ws.cell(row=row, column=13, value="Link")
        cm.hyperlink = birdeye_url
        cm.font = Font(color="0070C0", underline="single", size=10)
        cm.fill = row_fill; cm.border = _thin_border(); cm.alignment = _center()


# ===========================================================================
# Public entry point
# ===========================================================================

def export_wallets_excel(
    stats_list: list[WalletStats],
    results_dir: str = None,
    filename: str = "",
) -> str:
    from datetime import datetime
    results_dir = results_dir or settings.RESULTS_DIR
    os.makedirs(results_dir, exist_ok=True)

    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"wallets_{ts}.xlsx"

    path = os.path.join(results_dir, filename)

    wb = Workbook()
    # Summary sheet (default sheet)
    ws_summary = wb.active
    _build_summary_sheet(ws_summary, stats_list)

    # Per-wallet sheets
    for s in stats_list:
        try:
            _build_wallet_sheet(wb, s)
        except Exception as e:
            logger.error("Failed to build sheet for %s: %s", s.wallet, e)

    wb.save(path)
    logger.info("Saved Excel to %s", path)
    return path
