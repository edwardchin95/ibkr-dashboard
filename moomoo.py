import pandas as pd
import io
import os
import re
import csv as _csv
from datetime import datetime, timedelta

from app import (
    SNAPSHOT_DIR, HISTORY_FILE, INCOMING_DIR,
    TRADES_HISTORY_FILE,
    INDEX_ETFS,
    TARGET_ETF_STOCK_TOTAL, TARGET_SINGLE_STOCK,
    TARGET_OPTION_TOTAL, TARGET_CASH,
    OPTION_TARGETS, OPTION_COLORS,
    OPTION_MULTIPLIER,
    DEFAULT_USDSGD, DEFAULT_HKDSGD,
    UNIFIED_POSITIONS_COLS, UNIFIED_TRADES_COLS,
    JOURNAL_COLS, UNIFIED_HISTORY_COLS,
)


# ============================================================
# Moomoo-specific Constants
# ============================================================

MOOMOO_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "moomoo")
os.makedirs(MOOMOO_SNAPSHOT_DIR, exist_ok=True)

# ⭐ How many days after statement's end_date we still consider its
# [Account Overview] snapshot to be "fresh"
STATEMENT_FRESHNESS_DAYS = 7


# ============================================================
# Helpers
# ============================================================

def _safe_float(value, default=0):
    try:
        if value is None or pd.isna(value):
            return default
        if isinstance(value, str):
            v = value.replace(",", "").replace("$", "").strip()
            if v == "":
                return default
            return float(v)
        return float(value)
    except:
        return default


def _safe_str(value, default=""):
    try:
        if value is None or pd.isna(value):
            return default
        return str(value).strip()
    except:
        return default


def _read_text(file_obj):
    file_obj.seek(0)
    if hasattr(file_obj, "getvalue"):
        raw = file_obj.getvalue()
    else:
        raw = file_obj.read()
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "utf-8", "latin1"):
        try:
            return raw.decode(enc)
        except:
            continue
    return raw.decode("utf-8", errors="ignore")


def _convert_to_sgd(amount, currency, usd_to_sgd=DEFAULT_USDSGD, hkd_to_sgd=DEFAULT_HKDSGD):
    cur = _safe_str(currency).upper()
    if cur == "SGD":
        return amount
    if cur == "USD":
        return amount * usd_to_sgd
    if cur == "HKD":
        return amount * hkd_to_sgd
    return amount


# ============================================================
# OPTION PARSING (e.g. MARA260724C17500)
# ============================================================

def _parse_option_code(code):
    raw = _safe_str(code).upper()
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d+)$", raw)
    if not m:
        return None

    underlying = m.group(1)
    yymmdd = m.group(2)
    cp = m.group(3)
    strike_raw = m.group(4)

    try:
        year = "20" + yymmdd[:2]
        month = yymmdd[2:4]
        day = yymmdd[4:6]
        expiry_dt = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
        expiry = expiry_dt.strftime("%Y-%m-%d")
        dte = (expiry_dt - datetime.now()).days
    except:
        expiry = ""
        dte = None

    put_call = "C" if cp == "C" else "P"

    try:
        strike = float(strike_raw) / 1000
    except:
        strike = 0

    return {
        "underlying": underlying,
        "expiry": expiry,
        "put_call": put_call,
        "strike": strike,
        "dte": dte,
    }


def _is_option_symbol(symbol):
    return _parse_option_code(symbol) is not None


def _calc_dte(expiry_str):
    try:
        es = _safe_str(expiry_str)
        if es == "" or es.lower() == "nan":
            return None
        if " " in es:
            es = es.split(" ")[0]
        try:
            dt = datetime.strptime(es, "%Y-%m-%d")
        except:
            if len(es) == 8 and es.isdigit():
                dt = datetime.strptime(es, "%Y%m%d")
            else:
                return None
        return (dt - datetime.now()).days
    except:
        return None


# ============================================================
# DETECT
# ============================================================

def detect_moomoo_csv(file_obj_or_bytes):
    try:
        if isinstance(file_obj_or_bytes, bytes):
            text = file_obj_or_bytes.decode("utf-8-sig", errors="ignore")
        elif isinstance(file_obj_or_bytes, str):
            text = file_obj_or_bytes
        else:
            text = _read_text(file_obj_or_bytes)

        if "Moomoo Statement" in text:
            return True
        return False
    except:
        return False


# ============================================================
# READ SECTIONS FROM STATEMENT
# ============================================================

def _split_sections(text):
    lines = text.splitlines()
    meta = {}
    sections = {}

    current_section = None
    section_lines = []

    section_pat = re.compile(r"^\s*\[([^\]]+)\]\s*$")

    for line in lines:
        sm = section_pat.match(line.strip())
        if sm:
            if current_section is not None:
                sections[current_section] = section_lines
            current_section = sm.group(1).strip()
            section_lines = []
            continue

        if current_section is None:
            try:
                parts = next(_csv.reader([line]))
            except:
                parts = []
            if len(parts) >= 2 and parts[0].strip() != "":
                meta[parts[0].strip()] = parts[1].strip()
        else:
            section_lines.append(line)

    if current_section is not None:
        sections[current_section] = section_lines

    return meta, sections


def _section_to_df(section_lines):
    if not section_lines:
        return pd.DataFrame()
    while section_lines and section_lines[-1].strip() == "":
        section_lines.pop()
    if not section_lines:
        return pd.DataFrame()

    text = "\n".join(section_lines)
    try:
        df = pd.read_csv(io.StringIO(text))
        df = df.dropna(how="all").reset_index(drop=True)
        return df
    except:
        return pd.DataFrame()


def _parse_statement(file_obj):
    """Returns (meta, account_df, holdings_df, trades_df, cash_flow_raw_df)."""
    text = _read_text(file_obj)
    meta, sections = _split_sections(text)

    account_df = _section_to_df(sections.get("Account Overview", []))
    holdings_df = _section_to_df(sections.get("Holdings", []))
    trades_df = _section_to_df(sections.get("Trades", []))
    cash_flow_raw_df = _section_to_df(sections.get("Cash Flow Raw", []))

    return meta, account_df, holdings_df, trades_df, cash_flow_raw_df


# ============================================================
# EXTRACT META / NAV / CASH FROM ACCOUNT OVERVIEW
# ============================================================

def _get_meta_fx(meta):
    usd = _safe_float(meta.get("UsdToSgd", DEFAULT_USDSGD), DEFAULT_USDSGD)
    hkd = _safe_float(meta.get("HkdToSgd", DEFAULT_HKDSGD), DEFAULT_HKDSGD)
    if usd == 0:
        usd = DEFAULT_USDSGD
    if hkd == 0:
        hkd = DEFAULT_HKDSGD
    return usd, hkd


def _is_statement_fresh(meta, max_days_stale=STATEMENT_FRESHNESS_DAYS):
    """
    ⭐ Check if statement's [Account Overview] data is "fresh".

    Moomoo's accinfo_query always returns TODAY's account state, not historical
    snapshots. So if a statement was GENERATED long after its DateRange ended,
    the NAV/Cash/PnL data is just today's snapshot stamped with an old label.
    """
    generated_str = _safe_str(meta.get("GeneratedAt", ""))
    date_range = _safe_str(meta.get("DateRange", ""))

    if not generated_str or not date_range:
        return True

    try:
        gen_dt = datetime.strptime(generated_str.split()[0], "%Y-%m-%d")
    except:
        return True

    parts = re.split(r"\s*(?:→|->|—|–|to)\s*", date_range)
    if len(parts) < 2:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-–—→]\s*(\d{4}-\d{2}-\d{2})$", date_range)
        if m:
            parts = [m.group(1), m.group(2)]
        else:
            return True

    end_str = parts[1].strip()

    try:
        end_dt = datetime.strptime(end_str[:10], "%Y-%m-%d")
    except:
        return True

    diff_days = (gen_dt - end_dt).days
    return diff_days <= max_days_stale


def _get_field(df, candidates, default=0):
    if df is None or df.empty:
        return default
    row = df.iloc[0]
    for c in candidates:
        if c in df.columns:
            v = _safe_float(row[c], None)
            if v is not None:
                return v
    return default


def _get_field_str(df, candidates, default=""):
    if df is None or df.empty:
        return default
    row = df.iloc[0]
    for c in candidates:
        if c in df.columns:
            v = _safe_str(row[c], None)
            if v not in (None, "", "nan"):
                return v
    return default


def extract_nav_cash(account_df, usd_to_sgd, hkd_to_sgd, holdings_df=None):
    """
    Extract NAV (SGD), Cash (SGD) from accinfo_query result.
    ⭐ Cash = native cash + fund_assets (MM Fund balance included)
    """
    if account_df is None or account_df.empty:
        if holdings_df is not None and not holdings_df.empty:
            nav = pd.to_numeric(holdings_df["PositionValueSgd"], errors="coerce").fillna(0).sum()
            return float(nav), 0.0
        return 0.0, 0.0

    nav_native = _get_field(
        account_df,
        ["total_assets", "total_assets_value", "net_asset_value",
         "assets", "total_market_value", "totalAssets"],
        0,
    )

    cash_native = _get_field(
        account_df,
        ["cash", "available_cash", "available_funds", "total_cash", "cash_value"],
        0,
    )

    fund_assets_native = _get_field(
        account_df,
        ["fund_assets", "fund_value", "fund_balance"],
        0,
    )

    base_currency = _safe_str(_get_field_str(account_df, ["currency", "Currency"], "USD"), "USD").upper()

    nav_sgd = _convert_to_sgd(nav_native, base_currency, usd_to_sgd, hkd_to_sgd)
    cash_sgd = _convert_to_sgd(cash_native, base_currency, usd_to_sgd, hkd_to_sgd)
    fund_assets_sgd = _convert_to_sgd(fund_assets_native, base_currency, usd_to_sgd, hkd_to_sgd)

    # ⭐ Cash includes MM Fund balance
    total_cash_sgd = cash_sgd + fund_assets_sgd
    return float(nav_sgd), float(total_cash_sgd)


def extract_total_pnl(holdings_df):
    if holdings_df is None or holdings_df.empty:
        return 0.0
    if "UnrealizedPnLSgd" in holdings_df.columns:
        return float(pd.to_numeric(holdings_df["UnrealizedPnLSgd"], errors="coerce").fillna(0).sum())
    return 0.0


# ============================================================
# ⭐ CASHFLOW CATEGORIZATION
# ============================================================

def _categorize_cashflow(cf_type, cf_remark):
    """
    Classify a single cash flow row.
    Returns one of: 'deposit', 'withdrawal', 'dividend',
                    'withholding_tax', 'fee', 'other', 'ignore'
    """
    cf_type_s = _safe_str(cf_type)
    cf_remark_s = _safe_str(cf_remark)
    text = f"{cf_type_s} {cf_remark_s}".upper()
    cf_type_upper = cf_type_s.upper()
    remark_upper = cf_remark_s.upper()

    # 1. Withholding tax (priority)
    if any(k in text for k in [
        "WITHHOLDING TAX", "DIVIDEND TAX", "TAX ON DIVIDEND",
        "WITHHOLD",
        "预扣税", "預扣稅", "股息税", "股息稅",
    ]):
        return "withholding_tax"

    # 2. Dividend
    if any(k in text for k in [
        "CASH DIVIDEND", "DIVIDENDS",
        "股息", "派息", "紅利", "红利",
    ]):
        return "dividend"
    if cf_type_upper == "DIVIDEND":
        return "dividend"

    # 3. Bank Transfer
    if "BANK TRANSFER WITHDRAW" in text:
        return "withdrawal"
    if "BANK TRANSFER DEPOSIT" in text or "BANK TRANSFER IN" in text:
        return "deposit"

    # 4. Stock Yield Program → other
    if "STOCK YIELD" in text:
        return "other"

    # ⭐ 5. INTERNAL MOVEMENTS → ignore
    if "CURRENCY EXCHANGE" in cf_type_upper:
        return "ignore"
    if "AUTO CURRENCY EXCHANGE" in cf_type_upper:
        return "ignore"
    if any(k in remark_upper for k in [
        "TRANSFER FROM UNIVERSAL", "TRANSFER TO UNIVERSAL",
        "TRANSFER TO US STOCKS ACCOUNT", "TRANSFER FROM US STOCKS",
    ]):
        return "ignore"
    if "FUND SUBSCRIPTION" in text:
        return "ignore"
    if "FUND REDEMPTION" in text:
        return "ignore"

    # ⭐ 6. DEPOSIT IDENTIFICATION
    deposit_keywords = [
        "DDIIRGPC", "WISE_PAY", "DEPOSIT_OK",
        "COUPON DEPOSIT", "ACCOUNT UPGRADE",
    ]
    if any(k in remark_upper for k in deposit_keywords):
        return "deposit"

    # 7. Fees
    if any(k in text for k in [
        "COMMISSION", "PLATFORM FEE", "SETTLEMENT FEE",
        "REGULATORY FEE", "CLEARING FEE", "SEC FEE",
        "TRANSACTION FEE", "EXCHANGE FEE",
        "GST",
        "手续费", "手續費", "佣金",
    ]):
        return "fee"

    # ⭐ 8. USD/HKD "Others" → ignore (trade settlement)
    if cf_type_s == "Others":
        return "ignore"

    return "other"


def compute_cash_summary_from_raw(cash_flow_raw_df, usd_to_sgd, hkd_to_sgd):
    """⭐ Re-classify [Cash Flow Raw] using our own logic. Returns dict (SGD)."""
    result = {
        "deposits": 0.0, "withdrawals": 0.0,
        "dividends": 0.0, "withholding_tax": 0.0,
        "net_dividends": 0.0, "fees": 0.0, "other": 0.0,
        "stock_yield_income": 0.0,
        "misc_other": 0.0,
    }

    if cash_flow_raw_df is None or cash_flow_raw_df.empty:
        return result

    if "cashflow_type" not in cash_flow_raw_df.columns:
        return result

    for _, row in cash_flow_raw_df.iterrows():
        amt = _safe_float(row.get("cashflow_amount", 0))
        currency = _safe_str(row.get("currency", "USD")).upper()
        cf_type = _safe_str(row.get("cashflow_type", ""))
        cf_remark = _safe_str(row.get("cashflow_remark", ""))

        amt_sgd = _convert_to_sgd(amt, currency, usd_to_sgd, hkd_to_sgd)
        category = _categorize_cashflow(cf_type, cf_remark)

        if category == "deposit" and amt_sgd > 0:
            result["deposits"] += amt_sgd
        elif category == "withdrawal" and amt_sgd < 0:
            result["withdrawals"] += amt_sgd
        elif category == "dividend":
            result["dividends"] += amt_sgd
        elif category == "withholding_tax":
            result["withholding_tax"] += amt_sgd
        elif category == "fee":
            result["fees"] += amt_sgd
        elif category == "other":
            result["other"] += amt_sgd
            # ⭐ Sub-classify "other" into Stock Yield vs Misc
            text = f"{cf_type} {cf_remark}".upper()
            if "STOCK YIELD" in text:
                result["stock_yield_income"] += amt_sgd
            else:
                result["misc_other"] += amt_sgd
        # category == "ignore" → 跳过

    result["net_dividends"] = result["dividends"] + result["withholding_tax"]
    return result


# ============================================================
# EXTRACT REPORT DATE RANGE
# ============================================================

def extract_report_date_range(meta):
    rng = meta.get("DateRange", "")
    if not rng:
        return None, None

    parts = re.split(r"\s*(?:→|->|—|–|to)\s*", rng)
    if len(parts) < 2:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[-–—→]\s*(\d{4}-\d{2}-\d{2})$", rng)
        if m:
            parts = [m.group(1), m.group(2)]
        else:
            return None, None

    start = parts[0].strip()
    end = parts[1].strip()
    return start, end


# ============================================================
# POSITIONS PARSER (Unified Schema)
# ============================================================

def parse_moomoo_csv(file_obj):
    _, _, holdings_df, _, _ = _parse_statement(file_obj)

    if holdings_df is None or holdings_df.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)

    df = holdings_df.copy()

    for col in UNIFIED_POSITIONS_COLS:
        if col not in df.columns:
            df[col] = ""

    df = df[UNIFIED_POSITIONS_COLS]

    if "AssetClass" in df.columns:
        for i, row in df.iterrows():
            if _safe_str(row["AssetClass"]).upper() == "OPT":
                opt = _parse_option_code(row["Symbol"])
                if opt and opt["dte"] is not None:
                    df.at[i, "DTE"] = opt["dte"]

    return df


# ============================================================
# TRADES PARSER (Unified Schema)
# ============================================================

def parse_trades(file_obj, usd_to_sgd=None, hkd_to_sgd=None):
    meta, _, _, trades_df, _ = _parse_statement(file_obj)

    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    if usd_to_sgd is None or hkd_to_sgd is None:
        meta_usd, meta_hkd = _get_meta_fx(meta)
        if usd_to_sgd is None:
            usd_to_sgd = meta_usd
        if hkd_to_sgd is None:
            hkd_to_sgd = meta_hkd

    df = trades_df.copy()

    for col in UNIFIED_TRADES_COLS:
        if col not in df.columns:
            df[col] = ""

    for i, row in df.iterrows():
        cur = _safe_str(row.get("Currency", "USD")).upper()
        if cur == "USD" and (row.get("UsdToSgd", "") == "" or pd.isna(row.get("UsdToSgd", ""))):
            df.at[i, "UsdToSgd"] = usd_to_sgd

    df = df[UNIFIED_TRADES_COLS]
    return df


# ============================================================
# SAVE TRADES HISTORY
# ============================================================

def save_trades_history(file_obj, usd_to_sgd=None, hkd_to_sgd=None):
    new_trades = parse_trades(file_obj, usd_to_sgd=usd_to_sgd, hkd_to_sgd=hkd_to_sgd)

    if new_trades.empty:
        return load_trades_history()

    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            existing = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
        except:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    for col in UNIFIED_TRADES_COLS:
        if col not in existing.columns:
            existing[col] = ""
        if col not in new_trades.columns:
            new_trades[col] = ""

    existing = existing[UNIFIED_TRADES_COLS] if not existing.empty else existing
    new_trades = new_trades[UNIFIED_TRADES_COLS]

    key_cols = ["Platform", "TradeDate", "Symbol", "Buy/Sell", "Quantity", "TradePrice"]

    if not existing.empty:
        existing_journal = existing[key_cols + JOURNAL_COLS].copy()
        existing_journal = existing_journal.drop_duplicates(subset=key_cols, keep="last")
    else:
        existing_journal = pd.DataFrame(columns=key_cols + JOURNAL_COLS)

    combined = pd.concat([existing, new_trades], ignore_index=True)
    combined = combined.drop_duplicates(subset=key_cols, keep="last")

    combined = combined.merge(existing_journal, on=key_cols, how="left", suffixes=("", "_old"))
    for col in JOURNAL_COLS:
        old_col = f"{col}_old"
        if old_col in combined.columns:
            combined[col] = combined[col].combine_first(combined[old_col])
            combined.drop(columns=[old_col], inplace=True, errors="ignore")

    combined = _recompute_fifo_for_platform(combined, "Moomoo")

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
        df = df[df["Platform"] == "Moomoo"]

    if df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    return df[UNIFIED_TRADES_COLS]


# ============================================================
# FIFO RECOMPUTE
# ============================================================

def _recompute_fifo_for_platform(all_trades_df, platform):
    if all_trades_df is None or all_trades_df.empty:
        return all_trades_df

    df = all_trades_df.copy()

    mask = df["Platform"].astype(str) == platform
    if not mask.any():
        return df

    sub = df[mask].copy()

    sub["_qty"] = pd.to_numeric(sub["Quantity"], errors="coerce").fillna(0).abs()
    sub["_price"] = pd.to_numeric(sub["TradePrice"], errors="coerce").fillna(0)
    sub["_commission"] = pd.to_numeric(sub["Commission"], errors="coerce").fillna(0)
    sub["_usdsgd"] = pd.to_numeric(sub["UsdToSgd"], errors="coerce")
    sub["_usdsgd"] = sub["_usdsgd"].fillna(DEFAULT_USDSGD)

    sub = sub.sort_values("TradeDate", ascending=True)
    sub_index_order = sub.index.tolist()

    realized = {idx: 0.0 for idx in sub_index_order}

    for symbol in sub["Symbol"].unique():
        if not symbol or pd.isna(symbol):
            continue

        is_opt = _is_option_symbol(symbol)
        mult = OPTION_MULTIPLIER if is_opt else 1

        sym_idx_set = set(sub.index[sub["Symbol"] == symbol].tolist())
        sym_idx = [i for i in sub_index_order if i in sym_idx_set]

        open_queue = []

        for idx in sym_idx:
            row = sub.loc[idx]
            side = _safe_str(row["Buy/Sell"]).upper()
            qty = float(row["_qty"])
            price = float(row["_price"])
            commission = float(row["_commission"])

            if qty == 0 or side not in ("BUY", "SELL"):
                continue

            pnl = 0.0
            qty_left = qty

            while qty_left > 0 and open_queue and open_queue[0][0] != side:
                op_side, op_qty, op_price = open_queue[0]
                matched = min(qty_left, op_qty)

                if op_side == "BUY" and side == "SELL":
                    pnl += (price - op_price) * matched * mult
                elif op_side == "SELL" and side == "BUY":
                    pnl += (op_price - price) * matched * mult

                open_queue[0] = [op_side, op_qty - matched, op_price]
                qty_left -= matched

                if open_queue[0][1] <= 1e-9:
                    open_queue.pop(0)

            if qty_left > 0:
                open_queue.append([side, qty_left, price])

            pnl -= commission
            realized[idx] = pnl

    for idx in sub_index_order:
        r = realized[idx]
        df.at[idx, "RealizedPnL"] = r

        currency = _safe_str(df.at[idx, "Currency"]).upper()
        usd = _safe_float(df.at[idx, "UsdToSgd"], DEFAULT_USDSGD)
        if usd == 0:
            usd = DEFAULT_USDSGD

        if currency == "SGD":
            r_sgd = r
        elif currency == "USD":
            r_sgd = r * usd
        elif currency == "HKD":
            r_sgd = r * DEFAULT_HKDSGD
        else:
            r_sgd = r

        df.at[idx, "RealizedPnLSgd"] = r_sgd

    return df


# ============================================================
# ⭐ CUMULATIVE RECOMPUTE
# ============================================================

def _recompute_cumulative(history_df, platform):
    """Recompute Total* from cumsum of Period* (chronological order)."""
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
    upload_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    original_name = uploaded_file.name
    name_part, ext_part = os.path.splitext(original_name)

    uploaded_file.seek(0)
    meta, account_df, holdings_df, trades_df, cash_flow_raw_df = _parse_statement(uploaded_file)

    usd_to_sgd, hkd_to_sgd = _get_meta_fx(meta)

    cs = compute_cash_summary_from_raw(cash_flow_raw_df, usd_to_sgd, hkd_to_sgd)

    first_date, last_date = extract_report_date_range(meta)

    def _ymd(d):
        if not d:
            return ""
        s = str(d).replace("-", "").strip()
        return s if len(s) == 8 and s.isdigit() else ""

    fd = _ymd(first_date)
    ld = _ymd(last_date)

    if fd and ld:
        snapshot_filename = f"moomoo_statement({fd}-{ld}){ext_part}"
        timestamp = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
    else:
        snapshot_filename = f"{name_part}_{upload_time}{ext_part}"
        timestamp = upload_time

    snapshot_path = os.path.join(MOOMOO_SNAPSHOT_DIR, snapshot_filename)
    uploaded_file.seek(0)
    with open(snapshot_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    nav, cash = extract_nav_cash(account_df, usd_to_sgd, hkd_to_sgd, holdings_df=holdings_df)
    pnl = extract_total_pnl(holdings_df)

    # ⭐ Freshness check
    if not _is_statement_fresh(meta):
        nav = 0
        cash = 0
        pnl = 0

    deposit = cs["deposits"]
    withdrawal = cs["withdrawals"]
    other = cs["other"]

    if os.path.exists(HISTORY_FILE):
        try:
            history_df = pd.read_csv(HISTORY_FILE)
        except:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    new_row = pd.DataFrame([{
        "Platform": "Moomoo",
        "Timestamp": timestamp,
        "SnapshotFile": snapshot_filename,
        "NAV": nav,
        "Cash": cash,
        "PnL": pnl,
        "TotalDeposit": 0,
        "PeriodDeposit": deposit,
        "TotalWithdrawal": 0,
        "PeriodWithdrawal": withdrawal,
        "TotalOther": 0,
        "PeriodOther": other,
        "Dividends": cs["dividends"],
        "WithholdingTax": cs["withholding_tax"],
        "NetDividends": cs["net_dividends"],
        "Fees": cs["fees"],
        "UsdToSgd": usd_to_sgd,
    }])

    history_df = pd.concat([history_df, new_row], ignore_index=True)

    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    history_df = _recompute_cumulative(history_df, "Moomoo")

    history_df.to_csv(HISTORY_FILE, index=False)

    uploaded_file.seek(0)
    save_trades_history(uploaded_file, usd_to_sgd=usd_to_sgd, hkd_to_sgd=hkd_to_sgd)

    return history_df


# ============================================================
# ⭐ COMPUTE CUMULATIVE OTHERS BREAKDOWN (across all snapshots)
# ============================================================

def compute_cumulative_others_breakdown():
    """
    Loop through all Moomoo snapshot files and compute cumulative
    Stock Yield Income + Misc Other across ALL statements.

    Each statement's Cash Flow Raw is re-classified using its own
    UsdToSgd / HkdToSgd from meta (more accurate than global).
    """
    result = {
        "stock_yield_income": 0.0,
        "misc_other": 0.0,
    }

    if not os.path.exists(MOOMOO_SNAPSHOT_DIR):
        return result

    snapshot_files = [
        f for f in os.listdir(MOOMOO_SNAPSHOT_DIR)
        if f.lower().endswith(".csv")
    ]

    for f in snapshot_files:
        snapshot_path = os.path.join(MOOMOO_SNAPSHOT_DIR, f)
        try:
            with open(snapshot_path, "rb") as fh:
                fake_upload = io.BytesIO(fh.read())
                meta, _, _, _, cash_flow_raw_df = _parse_statement(fake_upload)
                stmt_usd, stmt_hkd = _get_meta_fx(meta)

                cs = compute_cash_summary_from_raw(cash_flow_raw_df, stmt_usd, stmt_hkd)
                result["stock_yield_income"] += cs["stock_yield_income"]
                result["misc_other"] += cs["misc_other"]
        except:
            continue

    return result


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
        moo_df = history_df[history_df["Platform"] == "Moomoo"]
    else:
        return None

    if moo_df.empty:
        return None

    def _extract_end_date(snap):
        s = str(snap)
        m = re.search(r"\(\d{8}-(\d{8})\)", s)
        if m:
            return m.group(1)
        return "00000000"

    moo_df = moo_df.copy()
    moo_df["_sort_key"] = moo_df["SnapshotFile"].apply(_extract_end_date)
    moo_df = moo_df.sort_values("_sort_key")
    moo_df = moo_df.drop(columns=["_sort_key"])

    latest = moo_df.iloc[-1]
    snapshot_file = latest["SnapshotFile"]

    snapshot_path = os.path.join(MOOMOO_SNAPSHOT_DIR, snapshot_file)
    if not os.path.exists(snapshot_path):
        legacy = os.path.join(SNAPSHOT_DIR, snapshot_file)
        if os.path.exists(legacy):
            snapshot_path = legacy
        else:
            return None

    with open(snapshot_path, "rb") as f:
        fake_upload = io.BytesIO(f.read())
        fake_upload.name = snapshot_file

        meta, account_df, holdings_df, _, _ = _parse_statement(fake_upload)
        usd_to_sgd, hkd_to_sgd = _get_meta_fx(meta)

        fake_upload.seek(0)
        df_positions = parse_moomoo_csv(fake_upload)

    # ⭐ Stock Yield + Misc Other are CUMULATIVE across all statements
    others = compute_cumulative_others_breakdown()

    return {
        "df_positions": df_positions,
        "history_df": moo_df,
        "nav": _safe_float(latest["NAV"], 0),
        "cash": _safe_float(latest["Cash"], 0),
        "pnl": _safe_float(latest["PnL"], 0),
        "deposit": _safe_float(latest.get("TotalDeposit", 0), 0),
        "withdrawal": _safe_float(latest.get("TotalWithdrawal", 0), 0),
        "other": _safe_float(latest.get("TotalOther", 0), 0),
        "stock_yield_income": others["stock_yield_income"],
        "misc_other": others["misc_other"],
        "usd_to_sgd": usd_to_sgd,
        "hkd_to_sgd": hkd_to_sgd,
        "platform": "Moomoo",
    }


# ============================================================
# PROCESS INCOMING
# ============================================================

def process_incoming():
    if not os.path.exists(INCOMING_DIR):
        return

    incoming_files = sorted([
        f for f in os.listdir(INCOMING_DIR)
        if f.lower().endswith(".csv")
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

            if not detect_moomoo_csv(fake_upload):
                continue

            fake_upload.seek(0)
            meta, account_df, holdings_df, trades_df, cash_flow_raw_df = _parse_statement(fake_upload)
            usd_to_sgd, hkd_to_sgd = _get_meta_fx(meta)

            cs = compute_cash_summary_from_raw(cash_flow_raw_df, usd_to_sgd, hkd_to_sgd)

            first_date, last_date = extract_report_date_range(meta)
            nav, cash = extract_nav_cash(account_df, usd_to_sgd, hkd_to_sgd, holdings_df=holdings_df)
            pnl = extract_total_pnl(holdings_df)

            # ⭐ Freshness check
            if not _is_statement_fresh(meta):
                nav = 0
                cash = 0
                pnl = 0

            deposit = cs["deposits"]
            withdrawal = cs["withdrawals"]
            other = cs["other"]

            fake_upload.seek(0)
            save_trades_history(fake_upload, usd_to_sgd=usd_to_sgd, hkd_to_sgd=hkd_to_sgd)

        def _ymd(d):
            if not d:
                return ""
            s = str(d).replace("-", "").strip()
            return s if len(s) == 8 and s.isdigit() else ""

        fd = _ymd(first_date)
        ld = _ymd(last_date)

        if fd and ld:
            new_name = f"moomoo_statement({fd}-{ld}).csv"
            timestamp = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
        else:
            new_name = f
            timestamp = f.replace("moomoo_statement_", "").replace(".csv", "")

        new_row = pd.DataFrame([{
            "Platform": "Moomoo",
            "Timestamp": timestamp,
            "SnapshotFile": new_name,
            "NAV": nav,
            "Cash": cash,
            "PnL": pnl,
            "TotalDeposit": 0,
            "PeriodDeposit": deposit,
            "TotalWithdrawal": 0,
            "PeriodWithdrawal": withdrawal,
            "TotalOther": 0,
            "PeriodOther": other,
            "Dividends": cs["dividends"],
            "WithholdingTax": cs["withholding_tax"],
            "NetDividends": cs["net_dividends"],
            "Fees": cs["fees"],
            "UsdToSgd": usd_to_sgd,
        }])

        history_df = pd.concat([history_df, new_row], ignore_index=True)

        snapshot_path = os.path.join(MOOMOO_SNAPSHOT_DIR, new_name)
        os.rename(incoming_path, snapshot_path)

    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    history_df = _recompute_cumulative(history_df, "Moomoo")

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
        "fx_ratio": 1.0, "stock_nav_sgd": 0, "option_nav_sgd": 0,
        "stock_pct_signed": 0, "option_pct_signed": 0,
        "option_pct_exposure": 0, "cash_pct": 0,
    }

    if df_positions is None or df_positions.empty:
        return defaults

    for _, row in df_positions.iterrows():
        symbol = _safe_str(row.get("Symbol", ""))
        asset_class = _safe_str(row.get("AssetClass", "")).upper()

        position_value_signed = _safe_float(row.get("PositionValueSgd", 0))
        position_value_abs = abs(position_value_signed)

        if asset_class == "OPT":
            option_total_signed += position_value_signed
            option_total_exposure += position_value_abs

            quantity = _safe_float(row.get("Quantity", 0))
            put_call = _safe_str(row.get("Put/Call", "")).strip().upper()
            underlying = _safe_str(row.get("UnderlyingSymbol", "")).strip()
            strike = row.get("Strike", "")
            expiry_str = _safe_str(row.get("Expiry", "")).strip()
            days_to_expiry = row.get("DTE", None)

            if days_to_expiry is None or pd.isna(days_to_expiry) or _safe_str(days_to_expiry) == "":
                days_to_expiry = _calc_dte(expiry_str)
            else:
                try:
                    days_to_expiry = int(float(days_to_expiry))
                except:
                    days_to_expiry = _calc_dte(expiry_str)

            if put_call.startswith("C"):
                put_call_norm = "C"
            elif put_call.startswith("P"):
                put_call_norm = "P"
            else:
                put_call_norm = put_call

            category = "Other Options"
            if quantity < 0 and put_call_norm == "P":
                category = "Sell Put"
            elif quantity < 0 and put_call_norm == "C":
                category = "Sell Call"
            elif quantity > 0 and put_call_norm == "C":
                if days_to_expiry is not None and days_to_expiry > 365:
                    category = "LEAPS Call"
                else:
                    category = "Long Call"
            elif quantity > 0 and put_call_norm == "P":
                category = "Long Put"

            if category not in option_categories:
                option_categories[category] = 0
            option_categories[category] += position_value_abs

            option_positions.append({
                "Category": category, "Underlying": underlying,
                "Symbol": symbol, "Put/Call": put_call_norm,
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

    stock_nav_sgd = stock_total_signed
    option_nav_sgd = option_total_signed

    stock_pct_signed = (stock_nav_sgd / total_nav_sgd * 100) if total_nav_sgd != 0 else 0
    option_pct_signed = (option_nav_sgd / total_nav_sgd * 100) if total_nav_sgd != 0 else 0
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
        "stock_nav_sgd": stock_nav_sgd,
        "option_nav_sgd": option_nav_sgd,
        "stock_pct_signed": stock_pct_signed,
        "option_pct_signed": option_pct_signed,
        "option_pct_exposure": option_pct_exposure,
        "cash_pct": cash_pct,
    }


# ============================================================
# CASH SUMMARY TOTAL
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

    if df.empty or "Platform" not in df.columns:
        return default

    df = df[df["Platform"] == "Moomoo"]
    if df.empty:
        return default

    for col in ["Dividends", "WithholdingTax", "NetDividends", "Fees",
                "PeriodDeposit", "PeriodWithdrawal", "PeriodOther"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

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
# REALIZED PNL SUMMARY
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