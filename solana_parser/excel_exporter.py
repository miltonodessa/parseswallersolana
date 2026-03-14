"""
Excel exporter — один лист "Summary" со всеми кошельками поочерёдно.

Структура для каждого кошелька (повторяется друг за другом):

  Row R+0  : ── разделитель с адресом кошелька ──────────────────────
  Row R+1  : заголовки колонок сводной строки
  Row R+2  : значения сводной строки (Balance, WR, PNL, ROI …)
  Row R+4  : блок статистики (AVG …) + таблица Profit/Distribution
  Row R+12 : заголовки таблицы токенов
  Row R+13+: строки токенов
  (пустые строки-разделитель перед следующим кошельком)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .wallet_analyzer import WalletStats, TokenTrade
import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
C_WALLET_BG   = "1F4E79"  # тёмно-синий  — разделитель кошелька
C_HEADER_BG   = "2E75B6"  # синий        — заголовки колонок
C_SUBHDR_BG   = "4472C4"  # средний синий — под-заголовки
C_STATS_BG    = "D6E4F0"  # очень светло-голубой — блок статистики
C_NEUTRAL_BG  = "F2F2F2"  # серый        — метки
C_ALT_ROW     = "EBF3FB"  # строки токенов (чётные)
C_GREEN_DARK  = "00B050"  # ROI > 500%
C_GREEN_MID   = "92D050"  # ROI 100-500%
C_GREEN_LIGHT = "C6EFCE"  # ROI 50-100%
C_YELLOW      = "FFEB9C"  # ROI 0-50%
C_RED_LIGHT   = "FFCCCC"  # ROI 0 … -50%
C_RED_DARK    = "FF0000"  # ROI < -50%
C_ORANGE      = "FF9900"  # предупреждение (Fast Trades / SMTB)
C_WHITE       = "FFFFFF"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, color="000000", size=10) -> Font:
    return Font(bold=bold, color=color, size=size)

def _center() -> Alignment:
    return Alignment(horizontal="center", vertical="center", wrap_text=False)

def _left() -> Alignment:
    return Alignment(horizontal="left", vertical="center", wrap_text=False)

def _thin() -> Border:
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)

def _roi_fill(roi: float) -> PatternFill:
    if roi > 500:  return _fill(C_GREEN_DARK)
    if roi > 100:  return _fill(C_GREEN_MID)
    if roi > 50:   return _fill(C_GREEN_LIGHT)
    if roi >= 0:   return _fill(C_YELLOW)
    if roi > -50:  return _fill(C_RED_LIGHT)
    return _fill(C_RED_DARK)

def _roi_font(roi: float) -> Font:
    dark = roi > 100 or roi < -50
    return _font(bold=True, color=C_WHITE if dark else "000000")

def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"

def _fmt_dur(sec: int) -> str:
    if sec <= 0:
        return "-"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) or f"{s}s"

def _set(ws, row, col, value, *, fill=None, font=None, align=None, border=None):
    """Write cell and apply styles."""
    cell = ws.cell(row=row, column=col, value=value)
    if fill:   cell.fill   = fill
    if font:   cell.font   = font
    if align:  cell.alignment = align
    if border: cell.border = border
    return cell

def _hdr(ws, row, col, value, bg=C_HEADER_BG):
    return _set(ws, row, col, value,
                fill=_fill(bg),
                font=_font(bold=True, color=C_WHITE),
                align=_center(),
                border=_thin())

def _lbl(ws, row, col, value):
    return _set(ws, row, col, value,
                fill=_fill(C_NEUTRAL_BG),
                font=_font(bold=True),
                align=_left(),
                border=_thin())

def _val(ws, row, col, value, *, bold=False, fill_hex=None):
    f = _fill(fill_hex) if fill_hex else _fill(C_WHITE)
    return _set(ws, row, col, value,
                fill=f,
                font=_font(bold=bold),
                align=_center(),
                border=_thin())

# ---------------------------------------------------------------------------
# Profit / Distribution buckets
# ---------------------------------------------------------------------------

_BUCKETS = [
    (">500%",     lambda r: r >  500,   C_GREEN_DARK,  C_WHITE),
    ("500-100%",  lambda r: 100 < r <= 500, C_GREEN_MID,  "000000"),
    ("100-50%",   lambda r: 50  < r <= 100, C_GREEN_LIGHT,"000000"),
    ("50-0%",     lambda r: 0   < r <= 50,  C_YELLOW,     "000000"),
    ("0%-50%",    lambda r: -50 < r <= 0,   C_RED_LIGHT,  "000000"),
    ("-50%-100%", lambda r: r  <= -50,   C_RED_DARK,   C_WHITE),
]

def _bucket_stats(trades: list[TokenTrade]) -> list[dict]:
    total = len(trades)
    out = []
    for label, pred, bg, fg in _BUCKETS:
        matched = [t for t in trades if pred(t.roi)]
        count = len(matched)
        out.append({
            "label": label,
            "count": count,
            "pct":   (count / total * 100) if total else 0.0,
            "pnl":   sum(t.pnl_sol for t in matched),
            "bg":    bg,
            "fg":    fg,
        })
    return out

# ---------------------------------------------------------------------------
# Column layout constants
# ---------------------------------------------------------------------------

# Summary header row columns
_SUMMARY_HEADERS = [
    ("Wallet",          46),
    ("Balance SOL",     12),
    ("Winrate %",       11),
    ("PNL SOL",         11),
    ("ROI %",           10),
    ("Spent SOL",       11),
    ("Earned SOL",      11),
    ("Fast Trades %",   13),
    ("SMTB %",          10),
    ("Tokens",           8),
    ("Trades/wk",       10),
    ("Score",            8),
]

# Token table columns (col A … M)
_TOKEN_HEADERS = [
    ("Token / Mint",     46),
    ("SPL Income",       14),
    ("SPL Outcome",      14),
    ("Spent SOL",        12),
    ("Earned SOL",       12),
    ("PNL SOL",          12),
    ("ROI %",            10),
    ("Duration",         12),
    ("Buys/Sells",       11),
    ("First Swap",       20),
    ("Last Swap",        20),
    ("GMGN",              8),
    ("Pool",              8),
]

N_COLS = len(_TOKEN_HEADERS)   # 13 columns

# ---------------------------------------------------------------------------
# Set column widths once (from TOKEN_HEADERS which is the widest)
# ---------------------------------------------------------------------------

def _set_col_widths(ws):
    for i, (_, w) in enumerate(_TOKEN_HEADERS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

# ---------------------------------------------------------------------------
# Write one wallet block starting at `start_row`; return next free row
# ---------------------------------------------------------------------------

def _write_wallet_block(ws, s: WalletStats, start_row: int) -> int:
    r = start_row
    total_spent  = sum(t.spent_sol  for t in s.token_trades)
    total_earned = sum(t.earned_sol for t in s.token_trades)
    avg_buys  = (sum(t.buys  for t in s.token_trades) / len(s.token_trades)) if s.token_trades else 0
    avg_sells = (sum(t.sells for t in s.token_trades) / len(s.token_trades)) if s.token_trades else 0

    # ── Row r: wallet address divider bar ───────────────────────────────
    ws.row_dimensions[r].height = 22
    cell = ws.cell(row=r, column=1, value=f"  {s.wallet}")
    cell.fill      = _fill(C_WALLET_BG)
    cell.font      = _font(bold=True, color=C_WHITE, size=11)
    cell.alignment = _left()
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=N_COLS)
    r += 1

    # ── Row r: summary column headers ───────────────────────────────────
    ws.row_dimensions[r].height = 24
    for ci, (label, _) in enumerate(_SUMMARY_HEADERS, 1):
        _hdr(ws, r, ci, label)
    r += 1

    # ── Row r: summary values ────────────────────────────────────────────
    ws.row_dimensions[r].height = 18
    vals = [
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
    for ci, v in enumerate(vals, 1):
        cell = ws.cell(row=r, column=ci, value=v)
        cell.border    = _thin()
        cell.alignment = _center() if ci > 1 else _left()
        cell.font      = _font(bold=(ci == 1))
        if ci == 5:    # ROI
            cell.fill = _roi_fill(s.roi);  cell.font = _roi_font(s.roi)
        elif ci == 4:  # PNL
            cell.fill = _fill(C_GREEN_LIGHT) if s.total_pnl_usd >= 0 else _fill(C_RED_LIGHT)
        elif ci == 8:  # Fast Trades
            cell.fill = _fill(C_ORANGE) if s.fast_trades_pct > settings.MAX_FAST_TRADES_PCT else _fill(C_WHITE)
        elif ci == 9:  # SMTB
            cell.fill = _fill(C_ORANGE) if s.smtb_pct > settings.MAX_SMTB_PCT else _fill(C_WHITE)
        else:
            cell.fill = _fill(C_WHITE)
    r += 1

    # ── blank row ────────────────────────────────────────────────────────
    r += 1

    # ── Rows r…r+6: stats panel (col A-B) + Profit/Distribution (col D-J) ──
    stats_panel = [
        ("AVG Trade Duration", _fmt_dur(
            int((s.last_trade_ts - s.first_trade_ts) / max(s.total_trades, 1))
            if s.first_trade_ts and s.last_trade_ts else 0)),
        ("AVG Buys",           round(avg_buys, 1)),
        ("AVG Sells",          round(avg_sells, 1)),
        ("AVG ROI %",          round(s.roi, 2)),
        ("Winrate %",          round(s.win_rate, 2)),
        ("SMTB SPL %",         round(s.smtb_pct, 2)),
        ("Fast Trades %",      round(s.fast_trades_pct, 2)),
        ("Balance SOL",        round(s.sol_balance, 4)),
        ("Trades / week",      round(s.trades_per_week, 2)),
        ("First Swap",         _fmt_ts(s.first_trade_ts)),
        ("Last Swap",          _fmt_ts(s.last_trade_ts)),
        ("Total Trades",       s.total_trades),
    ]
    stats_start = r
    for i, (lbl, v) in enumerate(stats_panel):
        row = stats_start + i
        ws.row_dimensions[row].height = 17
        lc = _lbl(ws, row, 1, lbl)
        vc = _val(ws, row, 2, v)
        # warn colours
        if lbl == "SMTB SPL %" and isinstance(v, float) and v > settings.MAX_SMTB_PCT:
            vc.fill = _fill(C_ORANGE)
        if lbl == "Fast Trades %" and isinstance(v, float) and v > settings.MAX_FAST_TRADES_PCT:
            vc.fill = _fill(C_ORANGE)

    # Profit/Distribution table sits in columns D(4)…J(10), starting at stats_start
    pd_row = stats_start

    # Title
    tc = ws.cell(row=pd_row, column=4, value="Profit / Distribution")
    tc.fill = _fill(C_SUBHDR_BG); tc.font = _font(bold=True, color=C_WHITE)
    tc.alignment = _center(); tc.border = _thin()
    ws.merge_cells(start_row=pd_row, start_column=4, end_row=pd_row, end_column=9)
    pd_row += 1

    # Bucket headers
    bucket_stats = _bucket_stats(s.token_trades)
    ws.row_dimensions[pd_row].height = 20
    for bi, b in enumerate(bucket_stats):
        cell = ws.cell(row=pd_row, column=4 + bi, value=b["label"])
        cell.fill = _fill(b["bg"]); cell.font = _font(bold=True, color=b["fg"])
        cell.alignment = _center(); cell.border = _thin()
    pd_row += 1

    # Count
    _lbl(ws, pd_row, 3, "Count")
    for bi, b in enumerate(bucket_stats):
        _val(ws, pd_row, 4 + bi, b["count"])
    pd_row += 1

    # Percent %
    _lbl(ws, pd_row, 3, "Percent %")
    for bi, b in enumerate(bucket_stats):
        _val(ws, pd_row, 4 + bi, round(b["pct"], 2))
    pd_row += 1

    # PnL SOL
    _lbl(ws, pd_row, 3, "PnL SOL")
    for bi, b in enumerate(bucket_stats):
        cell = _val(ws, pd_row, 4 + bi, round(b["pnl"], 4))
        cell.fill = _fill(C_GREEN_LIGHT) if b["pnl"] >= 0 else _fill(C_RED_LIGHT)
    pd_row += 1

    # Advance r past the taller of stats_panel vs PD table
    r = max(stats_start + len(stats_panel), pd_row) + 1

    # ── Token table header ───────────────────────────────────────────────
    ws.row_dimensions[r].height = 24
    for ci, (label, _) in enumerate(_TOKEN_HEADERS, 1):
        _hdr(ws, r, ci, label, bg=C_SUBHDR_BG)
    r += 1

    # ── Token rows ───────────────────────────────────────────────────────
    if not s.token_trades:
        ws.cell(row=r, column=1, value="Нет данных по токенам").font = _font(bold=True)
        r += 1
    else:
        for ti, t in enumerate(s.token_trades):
            ws.row_dimensions[r].height = 17
            row_bg = C_ALT_ROW if ti % 2 == 0 else C_WHITE

            # A: symbol or mint (short)
            name = t.symbol if t.symbol else t.mint[:16]
            ca = ws.cell(row=r, column=1, value=name)
            ca.fill = _fill(row_bg); ca.border = _thin(); ca.alignment = _left()
            ca.font = _font(size=10)

            # B: SPL Income (token units — not tracked, show "—")
            _val(ws, r, 2, "—", fill_hex=row_bg)

            # C: SPL Outcome
            _val(ws, r, 3, "—", fill_hex=row_bg)

            # D: Spent SOL
            _val(ws, r, 4, round(t.spent_sol, 4), fill_hex=row_bg)

            # E: Earned SOL
            _val(ws, r, 5, round(t.earned_sol, 4), fill_hex=row_bg)

            # F: PNL SOL — coloured
            cf = ws.cell(row=r, column=6, value=round(t.pnl_sol, 4))
            cf.fill = _fill(C_GREEN_LIGHT) if t.pnl_sol >= 0 else _fill(C_RED_LIGHT)
            cf.font = _font(bold=True); cf.border = _thin(); cf.alignment = _center()

            # G: ROI % — coloured
            cg = ws.cell(row=r, column=7, value=round(t.roi, 2))
            cg.fill = _roi_fill(t.roi); cg.font = _roi_font(t.roi)
            cg.border = _thin(); cg.alignment = _center()

            # H: Duration
            _val(ws, r, 8, _fmt_dur(t.duration_sec), fill_hex=row_bg)

            # I: Buys/Sells
            _val(ws, r, 9, f"{t.buys}/{t.sells}", fill_hex=row_bg)

            # J: First Swap
            _val(ws, r, 10, _fmt_ts(t.first_swap_ts), fill_hex=row_bg)

            # K: Last Swap
            _val(ws, r, 11, _fmt_ts(t.last_swap_ts), fill_hex=row_bg)

            # L: GMGN link
            cl = ws.cell(row=r, column=12, value="GMGN")
            cl.hyperlink = f"https://gmgn.ai/sol/token/{t.mint}"
            cl.font = Font(color="0070C0", underline="single", size=10)
            cl.fill = _fill(row_bg); cl.border = _thin(); cl.alignment = _center()

            # M: Birdeye link
            cm = ws.cell(row=r, column=13, value="Birdeye")
            cm.hyperlink = f"https://birdeye.so/token/{t.mint}?chain=solana"
            cm.font = Font(color="0070C0", underline="single", size=10)
            cm.fill = _fill(row_bg); cm.border = _thin(); cm.alignment = _center()

            r += 1

    # ── 3 blank rows before next wallet ─────────────────────────────────
    r += 3
    return r

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export_wallets_excel(
    stats_list: list[WalletStats],
    results_dir: str = None,
    filename: str = "",
) -> str:
    results_dir = results_dir or settings.RESULTS_DIR
    os.makedirs(results_dir, exist_ok=True)

    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"wallets_{ts}.xlsx"

    path = os.path.join(results_dir, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.freeze_panes = "A1"

    _set_col_widths(ws)

    current_row = 1
    for s in stats_list:
        try:
            current_row = _write_wallet_block(ws, s, current_row)
        except Exception as e:
            logger.error("Failed to write wallet %s: %s", s.wallet, e)

    wb.save(path)
    logger.info("Saved Excel → %s (%d wallets)", path, len(stats_list))
    return path
