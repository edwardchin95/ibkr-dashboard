"""
Tiger PDF statement parser (TABLE-BASED).

Handles BOTH Tiger PDF exports (web + mobile). The mobile PDF's raw text is
spatially scrambled, so we use pdfplumber's extract_tables() which respects
the real row/column structure. Output is identical to the CSV parser (tiger.py).

Writes to the SAME snapshots/tiger/ folder and the SAME
portfolio_history.csv / trades_history.csv.

Requires: pdfplumber
"""
import pandas as pd
import io
import os
import re

from app import (
    get_history_file,
    UNIFIED_POSITIONS_COLS, UNIFIED_TRADES_COLS,
)

from tiger import (
    get_tiger_snapshot_dir,
    _safe_float,
    _normalize_trade_date,
    _activity_to_buy_sell,
    _calc_dte,
    _recompute_cumulative,
    merge_and_save_trades,
    load_trades_history,
    load_latest_snapshot,
    analyze_positions,
    load_cash_summary_total, load_cash_summary_total_sgd,
    load_realized_pnl_summary, load_realized_pnl_summary_sgd,
    process_incoming,
)

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except Exception:
    _HAS_PDFPLUMBER = False


DEFAULT_USDSGD = 1.34


# ============================================================
# Small helpers
# ============================================================

def _f(s, default=0.0):
    try:
        return float(str(s).replace(",", "").replace("\n", " ").strip())
    except Exception:
        return default


def _clean(s):
    return str(s or "").replace("\n", " ").strip()


_OPT_ID_RE = re.compile(r"\(([A-Z.]+)\s+(\d{8})\s+(PUT|CALL)\s+([\d.]+)\)")


def _parse_symbol_cell(cell):
    """Return (symbol, expiry, right, strike, is_option) from a Symbol cell."""
    txt = _clean(cell)
    m = _OPT_ID_RE.search(txt)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4), True
    m2 = re.search(r"\(([A-Z0-9.]+)\)", txt)
    tkr = m2.group(1) if m2 else txt
    return tkr, "", "", "", False


def _is_total_row(cell):
    return _clean(cell).lower().startswith("total")


# ============================================================
# PDF loading (text + tables)
# ============================================================

def _pdf_pages(raw):
    """Return list of (text, tables) per page."""
    if not _HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber is not installed")
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            out.append((text, tables))
    return out


def _pdf_text(file_obj):
    """Full text of the PDF (used for detection + FX + NAV + cash summary)."""
    if not _HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber is not installed")
    file_obj.seek(0)
    raw = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj.read()
    parts = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def detect_tiger_pdf(file_obj_or_bytes):
    """True if the bytes are a PDF from Tiger Brokers."""
    try:
        if isinstance(file_obj_or_bytes, (bytes, bytearray)):
            raw = bytes(file_obj_or_bytes)
        elif hasattr(file_obj_or_bytes, "getvalue"):
            raw = file_obj_or_bytes.getvalue()
        elif hasattr(file_obj_or_bytes, "read"):
            file_obj_or_bytes.seek(0)
            raw = file_obj_or_bytes.read()
        else:
            return False
        if not raw[:5].startswith(b"%PDF"):
            return False
        if not _HAS_PDFPLUMBER:
            return False
        text = _pdf_text(io.BytesIO(raw))
        return "Tiger Brokers" in text
    except Exception:
        return False


def _raw_of(file_obj):
    file_obj.seek(0)
    return file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj.read()


# ============================================================
# DATE RANGE / FX / NAV / CASH SUMMARY  (from full text)
# ============================================================

def extract_report_date_range(text):
    m = re.search(r"Statement Period:\s*(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1), m.group(2)
    return None, None


def extract_fx_rates(text):
    fx = {"USD": 1.0}
    seg = text
    if "Base Currency Exchange Rate" in text:
        seg = text.split("Base Currency Exchange Rate", 1)[1]
    for cur, rate in re.findall(r"\b([A-Z]{3})\s+([\d]+\.[\d]+)\b", seg):
        if cur == "USD":
            continue
        fx[cur] = float(rate)
    return fx


def get_usd_to_sgd_rate(text):
    fx = extract_fx_rates(text)
    sgd_to_usd = fx.get("SGD", None)
    if sgd_to_usd and sgd_to_usd > 0:
        return 1.0 / sgd_to_usd
    return DEFAULT_USDSGD


def _to_sgd(amount, currency, usd_to_sgd, fx_rates):
    currency = str(currency).strip().upper()
    if currency == "SGD":
        return amount
    if currency == "USD":
        return amount * usd_to_sgd
    rate = fx_rates.get(currency, 1.0)  # currency -> USD
    return amount * rate * usd_to_sgd


def extract_nav_cash(text):
    m = re.search(
        r"End Of The Period\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+"
        r"([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)",
        text,
    )
    if not m:
        return 0.0, 0.0, 0.0, 0.0
    cash = _f(m.group(1))
    stock_nav = _f(m.group(2))
    option_nav = _f(m.group(3))
    total_nav = _f(m.group(7))
    return total_nav, cash, stock_nav, option_nav


def parse_cash_summary(text):
    block = text
    if "Currency: Base Currency Summary" in text:
        block = text.split("Currency: Base Currency Summary", 1)[1]
    m_end = re.search(r"Currency:\s*(USD|SGD|HKD)\b", block)
    if m_end:
        block = block[: m_end.start()]

    def grab(label):
        mm = re.search(re.escape(label) + r"\s+(-?[\d,]+\.?\d*)", block)
        return _f(mm.group(1)) if mm else 0.0

    dividends = grab("Dividends")
    withholding_tax = grab("Withholding Tax")
    commissions = grab("Commissions")
    platform_fees = grab("Platform Fees")
    gst = grab("GST")
    sec_fees = grab("SEC Fees")
    orf = grab("Option Regulatory Fees")
    clearing = grab("Clearing Fees")
    taf = grab("Trading Activity Fees")

    deposits = 0.0
    withdrawals = 0.0
    m_dep = re.search(r"\bDeposits\s+(-?[\d,]+\.?\d*)", block)
    if m_dep:
        v = _f(m_dep.group(1))
        if v >= 0:
            deposits = v
        else:
            withdrawals = v

    fees = commissions + platform_fees + gst + sec_fees + orf + clearing + taf

    return {
        "dividends": dividends,
        "withholding_tax": withholding_tax,
        "net_dividends": dividends + withholding_tax,
        "fees": fees,
        "commissions": commissions,
        "platform_fees": platform_fees,
        "gst": gst,
        "interest": 0.0,
        "deposits": deposits,
        "withdrawals": withdrawals,
    }


# ============================================================
# TABLE CLASSIFICATION
# ============================================================

def _header_names(tbl):
    return [_clean(c) for c in (tbl[0] if tbl else [])]


def _is_trades_table(tbl):
    h = _header_names(tbl)
    return "Activity Type" in h and "Trade Price" in h and "Symbol" in h and "Comm/Fee" in h


def _is_holdings_table(tbl):
    h = _header_names(tbl)
    return "Symbol" in h and "Close Price" in h and "Unrealized P/L" in h and "Multiplier" in h


# ============================================================
# POSITIONS (from holdings tables)
# ============================================================

def parse_positions(file_obj, usd_to_sgd=None):
    raw = _raw_of(file_obj)
    pages = _pdf_pages(raw)
    text = "\n".join(t for t, _ in pages)

    fx_rates = extract_fx_rates(text)
    if usd_to_sgd is None:
        usd_to_sgd = get_usd_to_sgd_rate(text)

    positions = []
    seen = set()

    for _, tables in pages:
        for tbl in tables:
            if not _is_holdings_table(tbl):
                continue
            hdr = _header_names(tbl)
            idx = {name: i for i, name in enumerate(hdr)}
            for row in tbl[1:]:
                sym_cell = row[idx.get("Symbol", 0)] if idx.get("Symbol") is not None else row[0]
                if _is_total_row(sym_cell):
                    continue
                symbol, expiry, right, strike, is_opt = _parse_symbol_cell(sym_cell)
                if not symbol:
                    continue

                qty = _f(row[idx.get("Quantity")])
                mult = _f(row[idx.get("Multiplier")], 1)
                cost = _f(row[idx.get("Cost Price")])
                close = _f(row[idx.get("Close Price")])
                value_native = _f(row[idx.get("Value")])
                upnl_native = _f(row[idx.get("Unrealized P/L")])
                cur = _clean(row[idx.get("Currency")]) or "USD"

                key = (symbol, expiry, right, strike, qty, value_native, cur)
                if key in seen:
                    continue
                seen.add(key)

                put_call = ""
                if is_opt:
                    put_call = "C" if right == "CALL" else "P"

                positions.append({
                    "Platform": "Tiger",
                    "Symbol": symbol,
                    "Description": (f"{symbol} {expiry} {right} {strike}" if is_opt else symbol),
                    "AssetClass": "OPT" if is_opt else "STK",
                    "Currency": cur,
                    "Quantity": qty,
                    "Multiplier": mult,
                    "CostPrice": cost,
                    "ClosePrice": close,
                    "PositionValue": value_native,
                    "PositionValueSgd": _to_sgd(value_native, cur, usd_to_sgd, fx_rates),
                    "UnrealizedPnL": upnl_native,
                    "UnrealizedPnLSgd": _to_sgd(upnl_native, cur, usd_to_sgd, fx_rates),
                    "UnderlyingSymbol": symbol,
                    "Put/Call": put_call,
                    "Strike": strike,
                    "Expiry": expiry,
                    "DTE": _calc_dte(expiry) if is_opt else None,
                })

    df = pd.DataFrame(positions)
    if df.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)
    keep = [c for c in UNIFIED_POSITIONS_COLS if c in df.columns]
    return df[keep]


# ============================================================
# NAV / CASH / PNL in SGD
# ============================================================

def _nav_cash_sgd_from_text_positions(text, df_positions, usd_to_sgd):
    _, cash_usd, _, _ = extract_nav_cash(text)
    if df_positions is None or df_positions.empty:
        total_nav_usd, _, stock_nav_usd, option_nav_usd = extract_nav_cash(text)
        return {
            "total_nav_sgd": total_nav_usd * usd_to_sgd,
            "cash_sgd": cash_usd * usd_to_sgd,
            "stock_nav_sgd": stock_nav_usd * usd_to_sgd,
            "option_nav_sgd": option_nav_usd * usd_to_sgd,
            "usd_to_sgd": usd_to_sgd,
        }
    stock_df = df_positions[df_positions["AssetClass"] == "STK"]
    stock_nav_sgd = float(stock_df["PositionValueSgd"].sum()) if not stock_df.empty else 0
    option_df = df_positions[df_positions["AssetClass"] == "OPT"]
    option_nav_sgd = float(option_df["PositionValueSgd"].sum()) if not option_df.empty else 0
    cash_sgd = cash_usd * usd_to_sgd
    return {
        "total_nav_sgd": stock_nav_sgd + option_nav_sgd + cash_sgd,
        "cash_sgd": cash_sgd,
        "stock_nav_sgd": stock_nav_sgd,
        "option_nav_sgd": option_nav_sgd,
        "usd_to_sgd": usd_to_sgd,
    }


def extract_total_pnl_from_positions(df_positions):
    if df_positions is None or df_positions.empty or "UnrealizedPnLSgd" not in df_positions.columns:
        return 0
    return float(df_positions["UnrealizedPnLSgd"].sum())


# ============================================================
# TRADES (from trades tables)
# ============================================================

def parse_trades(file_obj_or_text, usd_to_sgd=None, snapshot_file=""):
    # Accept either a file object or already-extracted (pages) — here always file obj.
    if isinstance(file_obj_or_text, str):
        # Backwards-compat: a raw text string won't have tables; return empty.
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    raw = _raw_of(file_obj_or_text)
    pages = _pdf_pages(raw)
    text = "\n".join(t for t, _ in pages)

    fx_rates = extract_fx_rates(text)
    if usd_to_sgd is None:
        usd_to_sgd = get_usd_to_sgd_rate(text)

    trades = []
    seen = set()

    for _, tables in pages:
        for tbl in tables:
            if not _is_trades_table(tbl):
                continue
            hdr = _header_names(tbl)
            idx = {name: i for i, name in enumerate(hdr)}
            for row in tbl[1:]:
                sym_cell = row[idx.get("Symbol", 0)]
                if _is_total_row(sym_cell):
                    continue
                symbol, expiry, right, strike, is_opt = _parse_symbol_cell(sym_cell)
                if not symbol:
                    continue  # blank continuation (doubled) row

                act = _clean(row[idx.get("Activity Type")])
                qty = _f(row[idx.get("Quantity")])
                price = _f(row[idx.get("Trade Price")])
                amount = _f(row[idx.get("Amount")])
                comm_blob = _clean(row[idx.get("Comm/Fee")])
                gst = _f(row[idx.get("GST")]) if idx.get("GST") is not None else 0.0
                rpl = _f(row[idx.get("Realized P/L")]) if idx.get("Realized P/L") is not None else 0.0
                ttime = _clean(row[idx.get("Trade Time")])
                cur = _clean(row[idx.get("Currency")]) or "USD"

                commission = 0.0
                cm = re.search(r"Commission:\s*(-?[\d.]+)", comm_blob)
                pf = re.search(r"Platform Fee:\s*(-?[\d.]+)", comm_blob)
                if cm:
                    commission += _f(cm.group(1))
                if pf:
                    commission += _f(pf.group(1))
                commission += gst

                key = (symbol, expiry, right, strike, act, qty, price, amount, ttime)
                if key in seen:
                    continue
                seen.add(key)

                buy_sell = _activity_to_buy_sell(act, quantity=qty)
                trade_date = _normalize_trade_date(ttime)
                asset_class = "OPT" if is_opt else "STK"
                desc = (f"{symbol} {expiry} {right} {strike}" if is_opt else symbol)

                trades.append({
                    "Platform": "Tiger",
                    "TradeDate": trade_date,
                    "SnapshotFile": snapshot_file,
                    "Symbol": symbol,
                    "Description": desc,
                    "AssetClass": asset_class,
                    "Buy/Sell": buy_sell,
                    "Quantity": qty,
                    "TradePrice": price,
                    "Currency": cur,
                    "Strategy": "",
                    "Notes": "",
                    "NetCash": amount,
                    "Commission": round(commission, 2),
                    "RealizedPnL": rpl,
                    "RealizedPnLSgd": _to_sgd(rpl, cur, usd_to_sgd, fx_rates),
                    "UsdToSgd": usd_to_sgd,
                })

    df = pd.DataFrame(trades)
    if df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)
    for col in UNIFIED_TRADES_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[UNIFIED_TRADES_COLS]


# ============================================================
# SAVE SNAPSHOT + HISTORY
# ============================================================

def save_snapshot_and_history(uploaded_file, *_args):
    from datetime import datetime
    upload_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    original_name = uploaded_file.name
    name_part, ext_part = os.path.splitext(original_name)
    if not ext_part:
        ext_part = ".pdf"

    raw = _raw_of(uploaded_file)
    pages = _pdf_pages(raw)
    text = "\n".join(t for t, _ in pages)

    first_date, last_date = extract_report_date_range(text)
    usd_to_sgd = get_usd_to_sgd_rate(text)

    def _ymd(d):
        return str(d).replace("-", "") if d else ""

    fd, ld = _ymd(first_date), _ymd(last_date)
    if fd and ld:
        snapshot_filename = f"tiger_statement({fd}-{ld}){ext_part}"
        _t = str(last_date)
        try:
            _dt = datetime.strptime(_t[:10], "%Y-%m-%d")
            timestamp = f"{_dt.day}/{_dt.month}/{_dt.year}"
        except Exception:
            timestamp = _t
    else:
        snapshot_filename = f"{name_part}_{upload_time}{ext_part}"
        timestamp = upload_time

    # Positions + NAV
    uploaded_file.seek(0)
    df_positions = parse_positions(uploaded_file, usd_to_sgd=usd_to_sgd)
    nav_data = _nav_cash_sgd_from_text_positions(text, df_positions, usd_to_sgd)
    nav_sgd = nav_data["total_nav_sgd"]
    cash_sgd = nav_data["cash_sgd"]
    pnl_sgd = extract_total_pnl_from_positions(df_positions)

    cash_summary = parse_cash_summary(text)
    deposit_sgd = cash_summary.get("deposits", 0) * usd_to_sgd
    withdrawal_sgd = cash_summary.get("withdrawals", 0) * usd_to_sgd
    period_other = 0

    # Store the original PDF in the tiger snapshot folder.
    snapshot_path = os.path.join(get_tiger_snapshot_dir(), snapshot_filename)
    uploaded_file.seek(0)
    with open(snapshot_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    if os.path.exists(get_history_file()):
        try:
            history_df = pd.read_csv(get_history_file())
        except Exception:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    dividends_usd = cash_summary.get("dividends", 0)
    withholding_tax_usd = cash_summary.get("withholding_tax", 0)
    fees_usd = cash_summary.get("fees", 0)
    net_dividends_usd = cash_summary.get("net_dividends", dividends_usd + withholding_tax_usd)

    new_row = pd.DataFrame([{
        "Platform": "Tiger",
        "Timestamp": timestamp,
        "SnapshotFile": snapshot_filename,
        "NAV": nav_sgd,
        "Cash": cash_sgd,
        "PnL": pnl_sgd,
        "TotalDeposit": 0,
        "PeriodDeposit": deposit_sgd,
        "TotalWithdrawal": 0,
        "PeriodWithdrawal": withdrawal_sgd,
        "TotalOther": 0,
        "PeriodOther": period_other,
        "Dividends": dividends_usd * usd_to_sgd,
        "WithholdingTax": withholding_tax_usd * usd_to_sgd,
        "NetDividends": net_dividends_usd * usd_to_sgd,
        "Fees": fees_usd * usd_to_sgd,
        "UsdToSgd": usd_to_sgd,
    }])

    history_df = pd.concat([history_df, new_row], ignore_index=True)
    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(subset=["Platform", "SnapshotFile"], keep="last")
    history_df = _recompute_cumulative(history_df, "Tiger")
    history_df.to_csv(get_history_file(), index=False)

    # Trades
    uploaded_file.seek(0)
    new_trades = parse_trades(uploaded_file, usd_to_sgd=usd_to_sgd, snapshot_file=snapshot_filename)
    merge_and_save_trades(new_trades, snapshot_filename)

    return history_df


# ============================================================
# LOAD FROM STORED PDF (used by tiger.load_latest_snapshot dispatch)
# ============================================================

def load_positions_from_pdf(path, usd_to_sgd=None):
    with open(path, "rb") as f:
        fake = io.BytesIO(f.read())
    text = _pdf_text(fake)
    if usd_to_sgd is None:
        usd_to_sgd = get_usd_to_sgd_rate(text)
    fake.seek(0)
    df_positions = parse_positions(fake, usd_to_sgd=usd_to_sgd)
    nav_data = _nav_cash_sgd_from_text_positions(text, df_positions, usd_to_sgd)
    return df_positions, nav_data
