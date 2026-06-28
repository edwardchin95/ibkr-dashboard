import pandas as pd
import io
import os
import re
import csv
from datetime import datetime

from app import (
    SNAPSHOT_DIR, HISTORY_FILE, INCOMING_DIR,
    TRADES_HISTORY_FILE,
    INDEX_ETFS,
    TARGET_ETF_STOCK_TOTAL, TARGET_SINGLE_STOCK,
    TARGET_OPTION_TOTAL, TARGET_CASH,
    OPTION_TARGETS, OPTION_COLORS,
    UNIFIED_POSITIONS_COLS, UNIFIED_TRADES_COLS,
    JOURNAL_COLS, UNIFIED_HISTORY_COLS,
)


# ============================================================
# Tiger-specific Constants
# ============================================================

TIGER_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "tiger")
os.makedirs(TIGER_SNAPSHOT_DIR, exist_ok=True)

# Alias for backward compatibility
TIGER_TRADES_HISTORY_FILE = TRADES_HISTORY_FILE


# ============================================================
# Helpers
# ============================================================

def _read_text(file_obj):
    file_obj.seek(0)
    if hasattr(file_obj, "getvalue"):
        raw = file_obj.getvalue()
    else:
        raw = file_obj.read()
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8-sig")
    except:
        try:
            return raw.decode("utf-8")
        except:
            return raw.decode("latin1")


def _read_rows(file_obj):
    text = _read_text(file_obj)
    reader = csv.reader(io.StringIO(text))
    rows = []
    for row in reader:
        clean = []
        for cell in row:
            if isinstance(cell, str):
                clean.append(cell.replace("\ufeff", "").strip())
            else:
                clean.append(cell)
        rows.append(clean)
    return rows


def _to_float(value, default=0):
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        v = str(value).strip()
        for ch in [",", "$", "USD", "SGD", "HKD"]:
            v = v.replace(ch, "")
        v = v.strip()
        if v == "" or v.lower() == "nan":
            return default
        return float(v)
    except:
        return default


def _safe_float(value, default=0):
    """Alias to match other modules."""
    return _to_float(value, default)


def _safe_get(row, idx, default=""):
    if idx is None:
        return default
    try:
        return row[idx]
    except:
        return default


def _parse_stock_symbol(description):
    desc = str(description).strip()
    if "(" in desc and ")" in desc:
        inside = desc.split("(")[-1].split(")")[0].strip()
        if inside:
            return inside
    return desc


def _parse_option_description(description):
    desc = str(description).strip()
    underlying = expiry = right = put_call = strike = ""

    if "(" in desc and ")" in desc:
        inside = desc.split("(")[-1].split(")")[0].strip()
        parts = inside.split()
        if len(parts) >= 4:
            underlying = parts[0].strip()
            expiry = parts[1].strip()
            right = parts[2].strip().upper()
            strike = parts[3].strip()
            if right == "CALL":
                put_call = "C"
            elif right == "PUT":
                put_call = "P"

    return {
        "Underlying": underlying, "Expiry": expiry,
        "Right": right, "Put/Call": put_call, "Strike": strike
    }


def _calc_dte(expiry_str):
    try:
        es = str(expiry_str).strip()
        if es == "" or es.lower() == "nan":
            return None
        if "." in es:
            es = es.split(".")[0]
        if len(es) == 6:
            es = "20" + es
        if "-" in es:
            expiry_date = datetime.strptime(es, "%Y-%m-%d")
        else:
            expiry_date = datetime.strptime(es, "%Y%m%d")
        return (expiry_date - datetime.now()).days
    except:
        return None


def _activity_to_buy_sell(activity_type, quantity=None):
    try:
        q = float(quantity)
        if q > 0:
            return "BUY"
        if q < 0:
            return "SELL"
    except:
        pass

    at = str(activity_type).strip().upper()

    if "BUY" in at:
        return "BUY"
    if "SELL" in at:
        return "SELL"

    if "OPENSHORT" in at or "CLOSELONG" in at:
        return "SELL"
    if "OPEN" in at or "CLOSE" in at:
        return "BUY"

    return ""


def _normalize_trade_date(trade_time):
    s = str(trade_time).strip()
    if s == "":
        return ""
    s = s.rstrip(",").strip()
    if " " in s:
        s = s.split(" ")[0]
    return s


def detect_tiger_csv(file_obj_or_bytes):
    try:
        if isinstance(file_obj_or_bytes, bytes):
            text = file_obj_or_bytes.decode("utf-8-sig", errors="ignore")
        elif isinstance(file_obj_or_bytes, str):
            text = file_obj_or_bytes
        else:
            text = _read_text(file_obj_or_bytes)

        if "Moomoo Statement" in text:
            return False

        if "Activity Statement" in text and "Tiger Brokers" in text:
            return True
        if "Account Overview" in text and "Cash Report" in text and "Holdings" in text:
            return True
        return False
    except:
        return False


# ============================================================
# FX RATES
# ============================================================

def extract_fx_rates(file_obj):
    rows = _read_rows(file_obj)
    fx_rates = {"USD": 1.0}

    for row in rows:
        if len(row) < 5:
            continue
        if (
            _safe_get(row, 0) == "Base Currency Exchange Rate"
            and _safe_get(row, 3) == "HEADER_DATA"
        ):
            currency = _safe_get(row, 4)
            rate = _to_float(_safe_get(row, 5), None)
            if currency and rate is not None:
                fx_rates[currency] = rate

    return fx_rates


def get_usd_to_sgd_rate(file_obj):
    fx_rates = extract_fx_rates(file_obj)
    sgd_to_usd = fx_rates.get("SGD", None)
    if sgd_to_usd and sgd_to_usd > 0:
        return 1.0 / sgd_to_usd
    return 1.34


def _convert_to_base(amount, currency, fx_rates):
    currency = str(currency).strip()
    rate = fx_rates.get(currency, 1.0)
    return amount * rate


def _to_sgd(amount, currency, usd_to_sgd, fx_rates):
    """Convert any currency to SGD."""
    currency = str(currency).strip().upper()
    if currency == "SGD":
        return amount
    if currency == "USD":
        return amount * usd_to_sgd
    base = _convert_to_base(amount, currency, fx_rates)
    return base * usd_to_sgd


# ============================================================
# EXTRACT REPORT DATE RANGE
# ============================================================

def extract_report_date_range(file_obj):
    rows = _read_rows(file_obj)

    for row in rows:
        if len(row) == 0:
            continue
        first = _safe_get(row, 0)
        if first.startswith("Activity Statement"):
            full_line = ",".join(row)
            if " - " in full_line:
                parts = full_line.split(",")
                for p in parts:
                    if " - " in p:
                        date_parts = p.strip().split(" - ")
                        if len(date_parts) == 2:
                            return date_parts[0].strip(), date_parts[1].strip()

    return None, None


# ============================================================
# EXTRACT NAV + CASH (raw USD base — internal use)
# ============================================================

def extract_nav_cash(file_obj):
    rows = _read_rows(file_obj)

    has_option = False
    for row in rows:
        if (
            _safe_get(row, 0) == "Account Overview"
            and "Cash" in row
            and "Stock" in row
        ):
            has_option = "Option" in row
            break

    for row in rows:
        if _safe_get(row, 0) != "Account Overview":
            continue
        if _safe_get(row, 3) != "DATA":
            continue
        if _safe_get(row, 4) != "End Of The Period":
            continue

        cash = _to_float(_safe_get(row, 5))
        stock_nav = _to_float(_safe_get(row, 6))
        option_nav = _to_float(_safe_get(row, 7)) if has_option else 0
        total_nav = _to_float(_safe_get(row, len(row) - 1))

        return total_nav, cash, stock_nav, option_nav

    return 0, 0, 0, 0


# ============================================================
# EXTRACT NAV + CASH SGD
# ============================================================

def extract_nav_cash_sgd(file_obj):
    file_obj.seek(0)
    usd_to_sgd = get_usd_to_sgd_rate(file_obj)

    file_obj.seek(0)
    _, cash_usd, _, _ = extract_nav_cash(file_obj)

    file_obj.seek(0)
    df_positions = parse_tiger_csv(file_obj, usd_to_sgd=usd_to_sgd)

    if df_positions is None or df_positions.empty:
        file_obj.seek(0)
        total_nav_usd, _, stock_nav_usd, option_nav_usd = extract_nav_cash(file_obj)
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
    total_nav_sgd = stock_nav_sgd + option_nav_sgd + cash_sgd

    return {
        "total_nav_sgd": total_nav_sgd,
        "cash_sgd": cash_sgd,
        "stock_nav_sgd": stock_nav_sgd,
        "option_nav_sgd": option_nav_sgd,
        "usd_to_sgd": usd_to_sgd,
    }


# ============================================================
# EXTRACT TOTAL PNL (Unrealized SGD)
# ============================================================

def extract_total_pnl(file_obj):
    file_obj.seek(0)
    usd_to_sgd = get_usd_to_sgd_rate(file_obj)

    file_obj.seek(0)
    df_positions = parse_tiger_csv(file_obj, usd_to_sgd=usd_to_sgd)

    if df_positions is None or df_positions.empty:
        return 0
    if "UnrealizedPnLSgd" not in df_positions.columns:
        return 0

    return float(df_positions["UnrealizedPnLSgd"].sum())


# ============================================================
# CASH SUMMARY (with withdrawal extraction)
# ============================================================

def parse_cash_summary(file_obj):
    """
    ⭐ Returns dict with deposits (positive) AND withdrawals (negative).
    Tiger statement uses "Deposits/Withdrawals" as a single category;
    we split by sign.
    """
    rows = _read_rows(file_obj)

    dividends = 0
    withholding_tax = 0
    commissions = 0
    platform_fees = 0
    gst = 0
    sec_fees = 0
    option_regulatory_fees = 0
    clearing_fees = 0
    trading_activity_fees = 0
    interest = 0
    deposits = 0
    withdrawals = 0

    in_base_summary = False

    for row in rows:
        if len(row) < 7:
            continue

        if (
            _safe_get(row, 0) == "Cash Report"
            and "Currency: Base Currency Summary" in row
        ):
            in_base_summary = True

        if (
            _safe_get(row, 0) == "Cash Report"
            and "Currency:" in ",".join(row)
            and "Base Currency Summary" not in ",".join(row)
            and in_base_summary
        ):
            break

        if not in_base_summary:
            continue
        if _safe_get(row, 3) != "HEADER_DATA":
            continue

        item = _safe_get(row, 4)
        amount = _to_float(_safe_get(row, 5))

        if item == "Dividends":
            dividends += amount
        elif item == "Withholding Tax":
            withholding_tax += amount
        elif item == "Commissions":
            commissions += amount
        elif item == "Platform Fees":
            platform_fees += amount
        elif item == "GST":
            gst += amount
        elif item == "SEC Fees":
            sec_fees += amount
        elif item == "Option Regulatory Fees":
            option_regulatory_fees += amount
        elif item == "Clearing Fees":
            clearing_fees += amount
        elif item == "Trading Activity Fees":
            trading_activity_fees += amount
        elif item == "Interest":
            interest += amount
        elif item in ["Deposits", "Deposits/Withdrawals", "Deposit", "Withdrawal"]:
            # ⭐ Split by sign
            if amount > 0:
                deposits += amount
            else:
                withdrawals += amount   # negative

    fees = (
        commissions + platform_fees + gst + sec_fees
        + option_regulatory_fees + clearing_fees + trading_activity_fees
    )

    return {
        "dividends": dividends,
        "withholding_tax": withholding_tax,
        "net_dividends": dividends + withholding_tax,
        "fees": fees,
        "commissions": commissions,
        "platform_fees": platform_fees,
        "gst": gst,
        "interest": interest,
        "deposits": deposits,
        "withdrawals": withdrawals,
    }


# ============================================================
# POSITIONS PARSER (Unified Schema)
# ============================================================

def parse_tiger_csv(file_obj, usd_to_sgd=None):
    rows = _read_rows(file_obj)
    fx_rates = extract_fx_rates(file_obj)

    if usd_to_sgd is None:
        sgd_to_usd = fx_rates.get("SGD", None)
        if sgd_to_usd and sgd_to_usd > 0:
            usd_to_sgd = 1.0 / sgd_to_usd
        else:
            usd_to_sgd = 1.34

    positions = []

    for row in rows:
        if len(row) < 14:
            continue
        if _safe_get(row, 0) != "Holdings":
            continue

        asset_type = _safe_get(row, 1)
        row_type = _safe_get(row, 3)

        if row_type != "DATA":
            continue

        description = _safe_get(row, 4)
        if description == "":
            continue

        quantity = _to_float(_safe_get(row, 5))
        multiplier = _to_float(_safe_get(row, 6), 1)
        cost_price = _to_float(_safe_get(row, 7))
        close_price = _to_float(_safe_get(row, 8))
        value_native = _to_float(_safe_get(row, 9))
        unrealized_native = _to_float(_safe_get(row, 10))
        currency = _safe_get(row, 13).strip()

        value_sgd = _to_sgd(value_native, currency, usd_to_sgd, fx_rates)
        unrealized_sgd = _to_sgd(unrealized_native, currency, usd_to_sgd, fx_rates)

        if asset_type == "Option":
            option_info = _parse_option_description(description)
            underlying = option_info["Underlying"]
            expiry = option_info["Expiry"]
            put_call = option_info["Put/Call"]
            strike = option_info["Strike"]
            dte = _calc_dte(expiry)
            symbol = underlying if underlying else description

            positions.append({
                "Platform": "Tiger",
                "Symbol": symbol,
                "Description": description,
                "AssetClass": "OPT",
                "Currency": currency,
                "Quantity": quantity,
                "Multiplier": multiplier,
                "CostPrice": cost_price,
                "ClosePrice": close_price,
                "PositionValue": value_native,
                "PositionValueSgd": value_sgd,
                "UnrealizedPnL": unrealized_native,
                "UnrealizedPnLSgd": unrealized_sgd,
                "UnderlyingSymbol": underlying,
                "Put/Call": put_call,
                "Strike": strike,
                "Expiry": expiry,
                "DTE": dte,
            })

        elif asset_type == "Stock":
            symbol = _parse_stock_symbol(description)

            positions.append({
                "Platform": "Tiger",
                "Symbol": symbol,
                "Description": description,
                "AssetClass": "STK",
                "Currency": currency,
                "Quantity": quantity,
                "Multiplier": multiplier,
                "CostPrice": cost_price,
                "ClosePrice": close_price,
                "PositionValue": value_native,
                "PositionValueSgd": value_sgd,
                "UnrealizedPnL": unrealized_native,
                "UnrealizedPnLSgd": unrealized_sgd,
                "UnderlyingSymbol": symbol,
                "Put/Call": "",
                "Strike": "",
                "Expiry": "",
                "DTE": None,
            })

    df = pd.DataFrame(positions)
    if df.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)

    keep = [c for c in UNIFIED_POSITIONS_COLS if c in df.columns]
    return df[keep]


# ============================================================
# TRADES PARSER (Unified Schema)
# ⭐ FIXED: multi-section header support
# ============================================================

def _is_trades_header_row(row):
    if len(row) < 5:
        return False
    if _safe_get(row, 0) != "Trades":
        return False
    if _safe_get(row, 1) != "":
        return False
    if _safe_get(row, 3) in ("DATA", "TOTAL"):
        return False
    has_activity = "Activity Type" in row
    has_symbol = any(s in row for s in ["Symbol", "Symbol(Base.Quote)"])
    return has_activity and has_symbol


def _build_idx_map(header):
    def find(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    return {
        "Symbol": find("Symbol"),
        "Symbol_Forex": find("Symbol(Base.Quote)"),
        "Activity Type": find("Activity Type"),
        "Quantity": find("Quantity"),
        "Quantity_Forex": find("Quantity(Base)"),
        "Trade Price": find("Trade Price"),
        "Amount": find("Amount"),
        "Amount_Forex": find("Amount(Quote)"),
        "Commission": find("Commission"),
        "Platform Fee": find("Platform Fee"),
        "GST": find("GST"),
        "Realized P/L": find("Realized P/L"),
        "Trade Time": find("Trade Time"),
        "Currency": find("Currency"),
    }


def parse_trades(file_obj, usd_to_sgd=None):
    rows = _read_rows(file_obj)
    fx_rates = extract_fx_rates(file_obj)

    if usd_to_sgd is None:
        file_obj.seek(0)
        usd_to_sgd = get_usd_to_sgd_rate(file_obj)

    trades = []

    current_header = None
    current_idx = None
    current_section_type = None

    for row in rows:
        if _is_trades_header_row(row):
            current_header = row
            current_idx = _build_idx_map(row)
            if "Symbol(Base.Quote)" in row:
                current_section_type = "Forex"
            else:
                current_section_type = "StockOrOption"
            continue

        if _safe_get(row, 0) != "Trades":
            continue

        if current_idx is None:
            continue

        asset_type = _safe_get(row, 1)
        row_type = _safe_get(row, 3)

        if row_type != "DATA":
            continue

        if asset_type == "Forex":
            continue

        if asset_type not in ("Stock", "Option"):
            continue

        description = _safe_get(row, current_idx.get("Symbol"))
        if description == "":
            continue

        asset_class = "OPT" if asset_type == "Option" else "STK"

        if asset_class == "OPT":
            option_info = _parse_option_description(description)
            symbol = option_info["Underlying"] if option_info["Underlying"] else description
        else:
            symbol = _parse_stock_symbol(description)

        activity_type = _safe_get(row, current_idx.get("Activity Type"))
        qty_raw = _safe_get(row, current_idx.get("Quantity"))
        buy_sell = _activity_to_buy_sell(activity_type, quantity=qty_raw)

        trade_time = _safe_get(row, current_idx.get("Trade Time"))
        trade_date = _normalize_trade_date(trade_time)

        quantity = _to_float(_safe_get(row, current_idx.get("Quantity")))
        trade_price = _to_float(_safe_get(row, current_idx.get("Trade Price")))
        currency = _safe_get(row, current_idx.get("Currency")) or "USD"
        net_cash = _to_float(_safe_get(row, current_idx.get("Amount")))

        commission = _to_float(_safe_get(row, current_idx.get("Commission")))
        platform_fee = _to_float(_safe_get(row, current_idx.get("Platform Fee")))
        gst = _to_float(_safe_get(row, current_idx.get("GST")))
        total_commission = commission + platform_fee + gst

        realized_pnl = _to_float(_safe_get(row, current_idx.get("Realized P/L")))
        realized_pnl_sgd = _to_sgd(realized_pnl, currency, usd_to_sgd, fx_rates)

        trades.append({
            "Platform": "Tiger",
            "TradeDate": trade_date,
            "Symbol": symbol,
            "Description": description,
            "AssetClass": asset_class,
            "Buy/Sell": buy_sell,
            "Quantity": quantity,
            "TradePrice": trade_price,
            "Currency": currency,
            "Strategy": "",
            "Notes": "",
            "NetCash": net_cash,
            "Commission": total_commission,
            "RealizedPnL": realized_pnl,
            "RealizedPnLSgd": realized_pnl_sgd,
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
# SAVE TRADES HISTORY
# ============================================================

def save_trades_history(file_obj, usd_to_sgd=None):
    file_obj.seek(0)
    if usd_to_sgd is None:
        usd_to_sgd = get_usd_to_sgd_rate(file_obj)
        file_obj.seek(0)

    new_trades = parse_trades(file_obj, usd_to_sgd=usd_to_sgd)

    if new_trades.empty:
        return load_trades_history()

    for col in UNIFIED_TRADES_COLS:
        if col not in new_trades.columns:
            new_trades[col] = ""

    new_trades = new_trades[UNIFIED_TRADES_COLS]

    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            existing = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
        except:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    if existing.empty:
        combined = new_trades.copy()

        for col in JOURNAL_COLS:
            if col not in combined.columns:
                combined[col] = ""

        combined = combined[UNIFIED_TRADES_COLS]

        if "TradeDate" in combined.columns:
            combined = combined.sort_values(
                ["Platform", "TradeDate", "Symbol"],
                ascending=[True, False, True]
            )

        combined.to_csv(TRADES_HISTORY_FILE, index=False)
        return load_trades_history()

    for col in UNIFIED_TRADES_COLS:
        if col not in existing.columns:
            existing[col] = ""

    for col in JOURNAL_COLS:
        if col not in existing.columns:
            existing[col] = ""

    existing = existing[UNIFIED_TRADES_COLS]

    key_cols = [
        "Platform",
        "TradeDate",
        "Symbol",
        "Buy/Sell",
        "Quantity",
        "TradePrice",
    ]

    for col in key_cols:
        if col not in existing.columns:
            existing[col] = ""
        if col not in new_trades.columns:
            new_trades[col] = ""

    existing_journal = existing[key_cols + JOURNAL_COLS].copy()
    existing_journal = existing_journal.drop_duplicates(
        subset=key_cols, keep="last"
    )

    combined = pd.concat([existing, new_trades], ignore_index=True)

    combined = combined.drop_duplicates(
        subset=key_cols, keep="last"
    )

    combined = combined.merge(
        existing_journal,
        on=key_cols,
        how="left",
        suffixes=("", "_old")
    )

    for col in JOURNAL_COLS:
        old_col = f"{col}_old"
        if old_col in combined.columns:
            combined[col] = combined[col].combine_first(combined[old_col])
            combined.drop(columns=[old_col], inplace=True, errors="ignore")
        if col not in combined.columns:
            combined[col] = ""

    for col in UNIFIED_TRADES_COLS:
        if col not in combined.columns:
            combined[col] = ""

    combined = combined[UNIFIED_TRADES_COLS]

    if "TradeDate" in combined.columns:
        combined = combined.sort_values(
            ["Platform", "TradeDate", "Symbol"],
            ascending=[True, False, True]
        )

    combined.to_csv(TRADES_HISTORY_FILE, index=False)

    return load_trades_history()


def load_trades_history():
    if not os.path.exists(TRADES_HISTORY_FILE):
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    try:
        df = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    if df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    for col in UNIFIED_TRADES_COLS:
        if col not in df.columns:
            df[col] = ""

    if "Platform" in df.columns:
        df = df[df["Platform"] == "Tiger"]

    if df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    return df[UNIFIED_TRADES_COLS]


# ============================================================
# ⭐ CUMULATIVE RECOMPUTE (cumsum-based, avoid re-upload bug)
# ============================================================

def _recompute_cumulative(history_df, platform):
    """Recompute Total* fields from cumsum of Period* values (chronological)."""
    if history_df is None or history_df.empty:
        return history_df
    if "Platform" not in history_df.columns:
        return history_df

    df = history_df.copy()

    mask = df["Platform"].astype(str) == platform
    if not mask.any():
        return df

    sub = df[mask].copy()

    def _extract_end_date(snap):
        s = str(snap)
        m = re.search(r"\(\d{8}-(\d{8})\)", s)
        if m:
            return m.group(1)
        return "00000000"

    sub["_sort_key"] = sub["SnapshotFile"].apply(_extract_end_date)
    sub = sub.sort_values("_sort_key")

    for total_col, period_col in [
        ("TotalDeposit", "PeriodDeposit"),
        ("TotalWithdrawal", "PeriodWithdrawal"),
        ("TotalOther", "PeriodOther"),
    ]:
        if period_col in sub.columns:
            period_vals = pd.to_numeric(sub[period_col], errors="coerce").fillna(0)
            sub[total_col] = period_vals.cumsum()

    sub = sub.drop(columns=["_sort_key"], errors="ignore")

    for col in ["TotalDeposit", "TotalWithdrawal", "TotalOther"]:
        if col in sub.columns:
            df.loc[sub.index, col] = sub[col]

    return df


# ============================================================
# SAVE SNAPSHOT + HISTORY
# ============================================================

def save_snapshot_and_history(uploaded_file, *_args):
    """Stores SGD values in history."""
    upload_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    original_name = uploaded_file.name
    name_part, ext_part = os.path.splitext(original_name)

    uploaded_file.seek(0)
    first_date, last_date = extract_report_date_range(uploaded_file)

    uploaded_file.seek(0)
    usd_to_sgd = get_usd_to_sgd_rate(uploaded_file)

    def _ymd(d):
        return str(d).replace("-", "") if d else ""

    fd = _ymd(first_date)
    ld = _ymd(last_date)

    if fd and ld:
        snapshot_filename = f"tiger_statement({fd}-{ld}){ext_part}"
        timestamp = last_date
    else:
        snapshot_filename = f"{name_part}_{upload_time}{ext_part}"
        timestamp = upload_time

    uploaded_file.seek(0)
    nav_data = extract_nav_cash_sgd(uploaded_file)
    nav_sgd = nav_data["total_nav_sgd"]
    cash_sgd = nav_data["cash_sgd"]

    uploaded_file.seek(0)
    pnl_sgd = extract_total_pnl(uploaded_file)

    uploaded_file.seek(0)
    cash_summary = parse_cash_summary(uploaded_file)

    # ⭐ Extract deposit + withdrawal (already in base USD, convert to SGD)
    deposit_usd = cash_summary.get("deposits", 0)
    withdrawal_usd = cash_summary.get("withdrawals", 0)
    deposit_sgd = deposit_usd * usd_to_sgd
    withdrawal_sgd = withdrawal_usd * usd_to_sgd
    period_other = 0  # Tiger doesn't have an "other" category

    snapshot_path = os.path.join(TIGER_SNAPSHOT_DIR, snapshot_filename)
    with open(snapshot_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    if os.path.exists(HISTORY_FILE):
        try:
            history_df = pd.read_csv(HISTORY_FILE)
        except:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    dividends_usd = cash_summary.get("dividends", 0)
    withholding_tax_usd = cash_summary.get("withholding_tax", 0)
    fees_usd = cash_summary.get("fees", 0)
    net_dividends_usd = cash_summary.get("net_dividends", dividends_usd + withholding_tax_usd)

    # ⭐ Add new row with Period* values; Total* recomputed via cumsum
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
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    # ⭐ Recompute Total* from cumsum
    history_df = _recompute_cumulative(history_df, "Tiger")

    history_df.to_csv(HISTORY_FILE, index=False)

    uploaded_file.seek(0)
    save_trades_history(uploaded_file, usd_to_sgd=usd_to_sgd)

    return history_df


# ============================================================
# LOAD LATEST SNAPSHOT
# ============================================================

def load_latest_snapshot():
    if not os.path.exists(HISTORY_FILE):
        return None

    try:
        history_df = pd.read_csv(HISTORY_FILE)
    except:
        return None

    if history_df.empty:
        return None

    if "Platform" in history_df.columns:
        tiger_df = history_df[history_df["Platform"] == "Tiger"]
    else:
        return None

    if tiger_df.empty:
        return None

    latest = tiger_df.iloc[-1]
    snapshot_file = latest["SnapshotFile"]
    snapshot_path = os.path.join(TIGER_SNAPSHOT_DIR, snapshot_file)

    if not os.path.exists(snapshot_path):
        return None

    usd_to_sgd = 1.34
    if "UsdToSgd" in history_df.columns:
        try:
            v = float(latest["UsdToSgd"])
            if v > 0:
                usd_to_sgd = v
        except:
            pass

    with open(snapshot_path, "rb") as f:
        fake_upload = io.BytesIO(f.read())
        fake_upload.name = snapshot_file

        nav_data = extract_nav_cash_sgd(fake_upload)

        fake_upload.seek(0)
        df_positions = parse_tiger_csv(fake_upload, usd_to_sgd=usd_to_sgd)

    return {
        "df_positions": df_positions,
        "history_df": tiger_df,
        "nav": latest["NAV"],
        "cash": latest["Cash"],
        "stock_nav": nav_data["stock_nav_sgd"],
        "option_nav": nav_data["option_nav_sgd"],
        "pnl": latest["PnL"],
        "deposit": _safe_float(latest.get("TotalDeposit", 0), 0),
        "withdrawal": _safe_float(latest.get("TotalWithdrawal", 0), 0),
        "other": _safe_float(latest.get("TotalOther", 0), 0),
        "usd_to_sgd": usd_to_sgd,
        "platform": "Tiger",
    }


# ============================================================
# PROCESS INCOMING
# ============================================================

def process_incoming():
    if not os.path.exists(INCOMING_DIR):
        return

    incoming_files = sorted([
        f for f in os.listdir(INCOMING_DIR)
        if f.endswith(".csv")
    ])

    if len(incoming_files) == 0:
        return

    if os.path.exists(HISTORY_FILE):
        try:
            history_df = pd.read_csv(HISTORY_FILE)
        except:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    for f in incoming_files:
        incoming_path = os.path.join(INCOMING_DIR, f)

        with open(incoming_path, "rb") as fh:
            raw = fh.read()
            fake_upload = io.BytesIO(raw)
            fake_upload.name = f

            if not detect_tiger_csv(fake_upload):
                continue

            fake_upload.seek(0)
            first_date, last_date = extract_report_date_range(fake_upload)

            fake_upload.seek(0)
            usd_to_sgd = get_usd_to_sgd_rate(fake_upload)

            fake_upload.seek(0)
            nav_data = extract_nav_cash_sgd(fake_upload)
            nav_sgd = nav_data["total_nav_sgd"]
            cash_sgd = nav_data["cash_sgd"]

            fake_upload.seek(0)
            pnl_sgd = extract_total_pnl(fake_upload)

            fake_upload.seek(0)
            cash_summary = parse_cash_summary(fake_upload)

            deposit_usd = cash_summary.get("deposits", 0)
            withdrawal_usd = cash_summary.get("withdrawals", 0)
            deposit_sgd = deposit_usd * usd_to_sgd
            withdrawal_sgd = withdrawal_usd * usd_to_sgd
            period_other = 0

            fake_upload.seek(0)
            save_trades_history(fake_upload, usd_to_sgd=usd_to_sgd)

        dividends_usd = cash_summary.get("dividends", 0)
        withholding_tax_usd = cash_summary.get("withholding_tax", 0)
        fees_usd = cash_summary.get("fees", 0)
        net_dividends_usd = cash_summary.get("net_dividends", dividends_usd + withholding_tax_usd)

        def _ymd(d):
            return str(d).replace("-", "") if d else ""

        fd = _ymd(first_date)
        ld = _ymd(last_date)

        if fd and ld:
            new_name = f"tiger_statement({fd}-{ld}).csv"
            timestamp = last_date
        else:
            new_name = f
            timestamp = f.replace("tiger_", "").replace(".csv", "")

        new_row = pd.DataFrame([{
            "Platform": "Tiger",
            "Timestamp": timestamp,
            "SnapshotFile": new_name,
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

        snapshot_path = os.path.join(TIGER_SNAPSHOT_DIR, new_name)
        os.rename(incoming_path, snapshot_path)

    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    # ⭐ Recompute Total* from cumsum
    history_df = _recompute_cumulative(history_df, "Tiger")

    history_df.to_csv(HISTORY_FILE, index=False)


# ============================================================
# ANALYZE POSITIONS
# ============================================================

def analyze_positions(df_positions, total_nav_sgd, cash_sgd):
    index_etf_total = 0
    stock_total = 0
    stock_total_signed = 0
    option_total_signed = 0
    option_total_exposure = 0

    index_etf_positions = []
    stock_positions = []

    option_categories = {
        "Sell Put": 0, "Sell Call": 0, "LEAPS Call": 0,
        "Long Call": 0, "Long Put": 0, "Other Options": 0
    }
    option_positions = []

    defaults = {
        "index_etf_total": 0, "stock_total": 0,
        "stock_total_signed": 0,
        "option_total_signed": 0, "option_total_exposure": 0,
        "index_etf_positions": [], "stock_positions": [],
        "option_categories": option_categories, "option_positions": [],
        "fx_ratio": 1.0, "stock_nav": 0, "option_nav": 0,
        "stock_pct_signed": 0, "option_pct_signed": 0,
        "option_pct_exposure": 0, "cash_pct": 0,
    }

    if df_positions is None or len(df_positions) == 0:
        return defaults

    for _, row in df_positions.iterrows():
        symbol = str(row.get("Symbol", ""))
        asset_class = str(row.get("AssetClass", ""))

        try:
            position_value_signed = float(row.get("PositionValueSgd", 0))
        except:
            position_value_signed = 0

        position_value_abs = abs(position_value_signed)

        if asset_class == "OPT":
            option_total_signed += position_value_signed
            option_total_exposure += position_value_abs

            try:
                quantity = float(row.get("Quantity", 0))
            except:
                quantity = 0

            put_call = str(row.get("Put/Call", "")).strip().upper()
            underlying = str(row.get("UnderlyingSymbol", "")).strip()
            strike = row.get("Strike", "")
            expiry_str = str(row.get("Expiry", "")).strip()
            days_to_expiry = row.get("DTE", None)

            category = "Other Options"
            if quantity < 0 and put_call == "P":
                category = "Sell Put"
            elif quantity < 0 and put_call == "C":
                category = "Sell Call"
            elif quantity > 0 and put_call == "C":
                if days_to_expiry is not None and days_to_expiry > 365:
                    category = "LEAPS Call"
                else:
                    category = "Long Call"
            elif quantity > 0 and put_call == "P":
                category = "Long Put"

            if category not in option_categories:
                option_categories[category] = 0
            option_categories[category] += position_value_abs

            option_positions.append({
                "Category": category, "Underlying": underlying,
                "Symbol": symbol, "Put/Call": put_call,
                "Quantity": quantity, "Strike": strike,
                "Expiry": expiry_str, "DTE": days_to_expiry,
                "SignedValue": position_value_signed,
                "Exposure": position_value_abs
            })

        elif symbol in INDEX_ETFS:
            index_etf_total += position_value_abs
            stock_total_signed += position_value_signed
            index_etf_positions.append({"Symbol": symbol, "Value": position_value_abs})

        else:
            stock_total += position_value_abs
            stock_total_signed += position_value_signed
            stock_positions.append({"Symbol": symbol, "Value": position_value_abs})

    stock_nav = stock_total_signed
    option_nav = option_total_signed

    stock_pct_signed = (stock_nav / total_nav_sgd * 100) if total_nav_sgd != 0 else 0
    option_pct_signed = (option_nav / total_nav_sgd * 100) if total_nav_sgd != 0 else 0
    option_pct_exposure = (option_total_exposure / total_nav_sgd * 100) if total_nav_sgd != 0 else 0
    cash_pct = (cash_sgd / total_nav_sgd * 100) if total_nav_sgd != 0 else 0

    return {
        "index_etf_total": index_etf_total,
        "stock_total": stock_total,
        "stock_total_signed": stock_total_signed,
        "option_total_signed": option_total_signed,
        "option_total_exposure": option_total_exposure,
        "index_etf_positions": index_etf_positions,
        "stock_positions": stock_positions,
        "option_categories": option_categories,
        "option_positions": option_positions,
        "fx_ratio": 1.0,
        "stock_nav": stock_nav,
        "option_nav": option_nav,
        "stock_pct_signed": stock_pct_signed,
        "option_pct_signed": option_pct_signed,
        "option_pct_exposure": option_pct_exposure,
        "cash_pct": cash_pct,
    }


# ============================================================
# CASH SUMMARY TOTAL (SGD)
# ============================================================

def load_cash_summary_total():
    return load_cash_summary_total_sgd()


def load_cash_summary_total_sgd():
    default = {
        "dividends": 0, "withholding_tax": 0, "net_dividends": 0,
        "fees": 0, "deposits": 0, "withdrawals": 0, "other": 0,
    }

    if not os.path.exists(HISTORY_FILE):
        return default

    try:
        df = pd.read_csv(HISTORY_FILE)
    except:
        return default

    if df.empty:
        return default

    if "Platform" in df.columns:
        df = df[df["Platform"] == "Tiger"]

    if df.empty:
        return default

    for col in ["Dividends", "WithholdingTax", "NetDividends", "Fees",
                "PeriodDeposit", "PeriodWithdrawal", "PeriodOther"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Sum Period* (robust to re-uploads)
    total_deposit = float(df["PeriodDeposit"].sum()) if "PeriodDeposit" in df.columns else 0
    total_withdrawal = float(df["PeriodWithdrawal"].sum()) if "PeriodWithdrawal" in df.columns else 0
    total_other = float(df["PeriodOther"].sum()) if "PeriodOther" in df.columns else 0

    return {
        "dividends": float(df["Dividends"].sum()) if "Dividends" in df.columns else 0,
        "withholding_tax": float(df["WithholdingTax"].sum()) if "WithholdingTax" in df.columns else 0,
        "net_dividends": float(df["NetDividends"].sum()) if "NetDividends" in df.columns else 0,
        "fees": float(df["Fees"].sum()) if "Fees" in df.columns else 0,
        "deposits": total_deposit,
        "withdrawals": total_withdrawal,
        "other": total_other,
    }


# ============================================================
# REALIZED PNL SUMMARY (SGD)
# ============================================================

def load_realized_pnl_summary():
    return load_realized_pnl_summary_sgd()


def load_realized_pnl_summary_sgd():
    trades_df = load_trades_history()

    if trades_df.empty:
        return {"realized_profit": 0, "realized_loss": 0, "realized_net": 0}

    if "RealizedPnLSgd" in trades_df.columns:
        col = "RealizedPnLSgd"
    elif "RealizedPnL" in trades_df.columns:
        col = "RealizedPnL"
    else:
        return {"realized_profit": 0, "realized_loss": 0, "realized_net": 0}

    pnl = pd.to_numeric(trades_df[col], errors="coerce").fillna(0)

    realized_profit = float(pnl[pnl > 0].sum())
    realized_loss = float(pnl[pnl < 0].sum())
    realized_net = float(pnl.sum())

    return {
        "realized_profit": realized_profit,
        "realized_loss": realized_loss,
        "realized_net": realized_net,
    }