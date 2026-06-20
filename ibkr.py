import pandas as pd
import io
import os
from datetime import datetime

from app import SNAPSHOT_DIR, HISTORY_FILE, INCOMING_DIR

# ============================================================
# Constants
# ============================================================

IBKR_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "ibkr")
os.makedirs(IBKR_SNAPSHOT_DIR, exist_ok=True)

TRADES_HISTORY_FILE = os.path.join(
    os.path.dirname(HISTORY_FILE),
    "ibkr_trades_history.csv"
)

INDEX_ETFS = ["CSPX", "VOO", "VT", "QQQ", "QQQM", "BNDW"]

TARGET_ETF_STOCK_TOTAL = 60
TARGET_SINGLE_STOCK = 10
TARGET_OPTION_TOTAL = 20
TARGET_CASH = 20

OPTION_TARGETS = {
    "Sell Put": 40,
    "Sell Call": 40,
    "LEAPS Call": 20,
    "Long Call": 10,
    "Long Put": 10,
    "Other Options": 0
}

OPTION_COLORS = {
    "Sell Put": "#4A7BFF",
    "Sell Call": "#00D4FF",
    "LEAPS Call": "#FFC300",
    "Long Call": "#00D4AA",
    "Long Put": "#FF6666",
    "Other Options": "#9CA3AF"
}

# ============================================================
# Unified Schema
# ============================================================

UNIFIED_POSITIONS_COLS = [
    "Platform", "Symbol", "Description", "AssetClass", "Currency",
    "Quantity", "Multiplier", "CostPrice", "ClosePrice",
    "PositionValue", "PositionValueSgd",
    "UnrealizedPnL", "UnrealizedPnLSgd",
    "UnderlyingSymbol", "Put/Call", "Strike", "Expiry", "DTE",
]

UNIFIED_TRADES_COLS = [
    "Platform", "TradeDate", "Symbol", "Description", "AssetClass",
    "Buy/Sell", "Quantity", "TradePrice", "Currency",
    "NetCash", "Commission",
    "RealizedPnL", "RealizedPnLSgd", "UsdToSgd",
]

UNIFIED_HISTORY_COLS = [
    "Platform", "Timestamp", "SnapshotFile",
    "NAV", "Cash", "PnL",
    "TotalDeposit", "PeriodDeposit",
    "Dividends", "WithholdingTax", "NetDividends", "Fees",
    "UsdToSgd",
]

# ============================================================
# Detect
# ============================================================

def detect_ibkr_csv(file_obj_or_bytes):
    try:
        if isinstance(file_obj_or_bytes, bytes):
            text = file_obj_or_bytes.decode("utf-8", errors="ignore")
        elif isinstance(file_obj_or_bytes, str):
            text = file_obj_or_bytes
        else:
            file_obj_or_bytes.seek(0)
            if hasattr(file_obj_or_bytes, "getvalue"):
                raw = file_obj_or_bytes.getvalue()
            else:
                raw = file_obj_or_bytes.read()
            if isinstance(raw, bytes):
                text = raw.decode("utf-8", errors="ignore")
            else:
                text = raw

        if "Tiger Brokers" in text:
            return False

        if '"AssetClass"' in text and "ClientAccountID" in text:
            return True

        return False
    except:
        return False


# ============================================================
# Helpers
# ============================================================

def _calc_dte(expiry_str):
    try:
        es = str(expiry_str).strip()
        if es == "" or es.lower() == "nan":
            return None
        if "." in es:
            es = es.split(".")[0]
        if len(es) == 6:
            es = "20" + es
        expiry_date = datetime.strptime(es, "%Y%m%d")
        return (expiry_date - datetime.now()).days
    except:
        return None


def _safe_float(value, default=0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except:
        return default


# ============================================================
# EXTRACT NAV + CASH
# ============================================================

def extract_nav_cash(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    nav_section = False
    nav_rows = []

    for line in lines:
        if '"CurrencyPrimary","ReportDate","Cash"' in line:
            nav_section = True
            continue
        if nav_section and line.startswith('"ReportDate"'):
            break
        if nav_section:
            parts = line.replace('"', '').split(",")
            if len(parts) == 14:
                nav_rows.append(parts)

    if len(nav_rows) == 0:
        return 0, 0, 0, 0

    latest = nav_rows[-1]

    try:
        cash_sgd = float(latest[2])
        stock_nav_sgd = float(latest[5])
        option_nav_sgd = float(latest[8])
        total_nav = float(latest[11])
    except:
        return 0, 0, 0, 0

    return total_nav, cash_sgd, stock_nav_sgd, option_nav_sgd


# ============================================================
# EXTRACT TOTAL PNL (Holding P&L, already SGD)
# ============================================================

def extract_total_pnl(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    pnl_rows = []
    pnl_section = False

    for line in lines:
        if '"ReportDate","TotalRealizedPnl","TotalUnrealizedPnl","TotalFifoPnl"' in line:
            pnl_section = True
            continue
        if pnl_section and line.startswith('"AssetClass"'):
            break
        if pnl_section:
            parts = line.replace('"', '').split(",")
            if len(parts) >= 4:
                pnl_rows.append(parts)

    if len(pnl_rows) == 0:
        return 0

    latest = pnl_rows[-1]
    try:
        total_fifo_pnl = float(latest[3])
    except:
        return 0

    return total_fifo_pnl


# ============================================================
# EXTRACT TOTAL DEPOSIT
# ============================================================

def extract_total_deposit(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    cash_section = False
    rows = []

    for line in lines:
        if '"CurrencyPrimary","AssetClass","Date/Time","Amount","Type","Description"' in line:
            cash_section = True
            continue
        if cash_section and line.startswith('"Description"'):
            break
        if cash_section:
            rows.append(line)

    if len(rows) == 0:
        return 0

    csv_text = "\n".join(rows)
    columns = ["Currency", "AssetClass", "DateTime", "Amount", "Type", "Description"]

    try:
        df = pd.read_csv(io.StringIO(csv_text), names=columns)
    except:
        return 0

    deposit_df = df[df["Type"] == "Deposits/Withdrawals"]
    if len(deposit_df) == 0:
        return 0

    total_deposit = deposit_df[deposit_df["Amount"] > 0]["Amount"].sum()
    return float(total_deposit)


# ============================================================
# EXTRACT REPORT DATE RANGE
# ============================================================

def extract_report_date_range(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    nav_section = False
    dates = []

    for line in lines:
        if '"CurrencyPrimary","ReportDate","Cash"' in line:
            nav_section = True
            continue
        if nav_section and line.startswith('"ReportDate"'):
            break
        if nav_section:
            parts = line.replace('"', '').split(",")
            if len(parts) >= 2:
                dates.append(parts[1])

    if len(dates) == 0:
        return None, None

    return dates[0], dates[-1]


# ============================================================
# COMPUTE FX RATIO (raw -> SGD)
# ============================================================

def _compute_fx_ratio(file_obj):
    """
    IBKR positions are in raw (USD) but NAV is in SGD.
    fx_ratio = invested_nav_sgd / sum(position_values)
    """
    file_obj.seek(0)
    raw_df = _parse_raw_positions(file_obj)

    file_obj.seek(0)
    total_nav, cash_sgd, _, _ = extract_nav_cash(file_obj)

    invested_nav_sgd = total_nav - cash_sgd

    if raw_df is None or raw_df.empty:
        return 1.0

    try:
        raw_pos_sum = pd.to_numeric(raw_df["PositionValue"], errors="coerce").fillna(0).sum()
    except:
        raw_pos_sum = 0

    if raw_pos_sum == 0:
        return 1.0

    return float(invested_nav_sgd / raw_pos_sum)


# ============================================================
# RAW POSITIONS PARSER (internal)
# ============================================================

def _parse_raw_positions(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    position_start = None
    for i, line in enumerate(lines):
        if '"AssetClass"' in line:
            position_start = i
            break

    if position_start is None:
        return None

    position_lines = lines[position_start:]

    filtered = []
    for line in position_lines:
        if line.startswith('"STK"') or line.startswith('"OPT"'):
            filtered.append(line)

    filtered.insert(0, lines[position_start])

    csv_text = "\n".join(filtered)

    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except:
        return None

    return df


# ============================================================
# POSITIONS PARSER (Unified Schema)
# ============================================================

def parse_ibkr_csv(file_obj):
    raw_df = _parse_raw_positions(file_obj)

    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)

    file_obj.seek(0)
    fx_ratio = _compute_fx_ratio(file_obj)

    positions = []

    for _, row in raw_df.iterrows():
        symbol = str(row.get("Symbol", ""))
        asset_class = str(row.get("AssetClass", ""))
        description = str(row.get("Description", "")) if pd.notna(row.get("Description", "")) else ""
        currency = str(row.get("CurrencyPrimary", "USD")) if pd.notna(row.get("CurrencyPrimary", "")) else "USD"

        quantity = _safe_float(row.get("Quantity", 0))
        multiplier = _safe_float(row.get("Multiplier", 1), 1)
        cost_price = _safe_float(row.get("CostPrice", 0))

        # ClosePrice or MarkPrice
        close_price = _safe_float(row.get("ClosePrice", row.get("MarkPrice", 0)))

        position_value = _safe_float(row.get("PositionValue", 0))
        position_value_sgd = position_value * fx_ratio

        unrealized = _safe_float(row.get("FifoPnlUnrealized", 0))
        unrealized_sgd = unrealized * fx_ratio

        underlying = str(row.get("UnderlyingSymbol", "")) if pd.notna(row.get("UnderlyingSymbol", "")) else ""
        put_call = str(row.get("Put/Call", "")) if pd.notna(row.get("Put/Call", "")) else ""
        strike = row.get("Strike", "") if pd.notna(row.get("Strike", "")) else ""
        expiry = str(row.get("Expiry", "")) if pd.notna(row.get("Expiry", "")) else ""
        dte = _calc_dte(expiry) if asset_class == "OPT" else None

        positions.append({
            "Platform": "IBKR",
            "Symbol": symbol,
            "Description": description,
            "AssetClass": asset_class,
            "Currency": currency,
            "Quantity": quantity,
            "Multiplier": multiplier,
            "CostPrice": cost_price,
            "ClosePrice": close_price,
            "PositionValue": position_value,
            "PositionValueSgd": position_value_sgd,
            "UnrealizedPnL": unrealized,
            "UnrealizedPnLSgd": unrealized_sgd,
            "UnderlyingSymbol": underlying,
            "Put/Call": put_call,
            "Strike": strike,
            "Expiry": expiry,
            "DTE": dte,
        })

    result = pd.DataFrame(positions)

    if result.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)

    keep = [c for c in UNIFIED_POSITIONS_COLS if c in result.columns]
    return result[keep]
# ============================================================
# SAVE SNAPSHOT + HISTORY
# ============================================================
def save_snapshot_and_history(uploaded_file, nav, cash, pnl, deposit):
    upload_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    original_name = uploaded_file.name
    name_part, ext_part = os.path.splitext(original_name)

    uploaded_file.seek(0)
    first_date, last_date = extract_report_date_range(uploaded_file)
    uploaded_file.seek(0)

    def _ymd(d):
        return str(d).replace("-", "") if d else ""

    fd = _ymd(first_date)
    ld = _ymd(last_date)

    if fd and ld:
        snapshot_filename = f"ibkr_statement({fd}-{ld}){ext_part}"
        if len(ld) == 8 and ld.isdigit():
            timestamp = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
        else:
            timestamp = str(last_date)
    else:
        snapshot_filename = f"{name_part}_{upload_time}{ext_part}"
        timestamp = upload_time

    snapshot_path = os.path.join(IBKR_SNAPSHOT_DIR, snapshot_filename)
    with open(snapshot_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    # Cash summary (already SGD in IBKR)
    uploaded_file.seek(0)
    cash_summary = parse_cash_summary(uploaded_file)
    uploaded_file.seek(0)

    # fx_ratio
    uploaded_file.seek(0)
    fx_ratio = _compute_fx_ratio(uploaded_file)
    uploaded_file.seek(0)

    dividends = cash_summary.get("dividends", 0)
    withholding_tax = cash_summary.get("withholding_tax", 0)
    fees = cash_summary.get("fees", 0)
    net_dividends = dividends + withholding_tax

    # Load existing history
    if os.path.exists(HISTORY_FILE):
        try:
            history_df = pd.read_csv(HISTORY_FILE)
        except:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    # Filter only IBKR rows for cumulative deposit
    previous_total_deposit = 0
    if not history_df.empty:
        if "Platform" in history_df.columns:
            ibkr_only = history_df[history_df["Platform"] == "IBKR"]
        else:
            ibkr_only = history_df
        if len(ibkr_only) > 0 and "TotalDeposit" in ibkr_only.columns:
            previous_total_deposit = ibkr_only.iloc[-1]["TotalDeposit"]

    cumulative_deposit = previous_total_deposit + deposit

    new_row = pd.DataFrame([{
        "Platform": "IBKR",
        "Timestamp": timestamp,
        "SnapshotFile": snapshot_filename,
        "NAV": nav,
        "Cash": cash,
        "PnL": pnl,
        "TotalDeposit": cumulative_deposit,
        "PeriodDeposit": deposit,
        "Dividends": dividends,
        "WithholdingTax": withholding_tax,
        "NetDividends": net_dividends,
        "Fees": fees,
        "UsdToSgd": fx_ratio,
    }])

    history_df = pd.concat([history_df, new_row], ignore_index=True)

    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    history_df.to_csv(HISTORY_FILE, index=False)

    # Save trades
    uploaded_file.seek(0)
    save_trades_history(uploaded_file, fx_ratio=fx_ratio)

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

    # Filter IBKR only
    if "Platform" in history_df.columns:
        ibkr_df = history_df[history_df["Platform"] == "IBKR"]
    else:
        ibkr_df = history_df

    if ibkr_df.empty:
        return None

    latest = ibkr_df.iloc[-1]
    snapshot_file = latest["SnapshotFile"]

    # New location first, fallback old location
    snapshot_path = os.path.join(IBKR_SNAPSHOT_DIR, snapshot_file)
    if not os.path.exists(snapshot_path):
        legacy = os.path.join(SNAPSHOT_DIR, snapshot_file)
        if os.path.exists(legacy):
            snapshot_path = legacy
        else:
            return None

    with open(snapshot_path, "rb") as f:
        fake_upload = io.BytesIO(f.read())
        fake_upload.name = snapshot_file

        total_nav_snap, cash_snap, stock_nav_snap, option_nav_snap = extract_nav_cash(fake_upload)

        fake_upload.seek(0)
        df_positions = parse_ibkr_csv(fake_upload)

    return {
        "df_positions": df_positions,
        "history_df": ibkr_df,
        "nav": latest["NAV"],
        "cash": latest["Cash"],
        "stock_nav_sgd": stock_nav_snap,
        "option_nav_sgd": option_nav_snap,
        "pnl": latest["PnL"],
        "deposit": latest["TotalDeposit"],
        "platform": "IBKR",
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

            if not detect_ibkr_csv(fake_upload):
                continue

            fake_upload.seek(0)
            first_date, last_date = extract_report_date_range(fake_upload)

            fake_upload.seek(0)
            nav, cash, stock_nav, option_nav = extract_nav_cash(fake_upload)

            fake_upload.seek(0)
            pnl = extract_total_pnl(fake_upload)

            fake_upload.seek(0)
            deposit = extract_total_deposit(fake_upload)

            fake_upload.seek(0)
            cash_summary = parse_cash_summary(fake_upload)

            fake_upload.seek(0)
            fx_ratio = _compute_fx_ratio(fake_upload)

            fake_upload.seek(0)
            save_trades_history(fake_upload, fx_ratio=fx_ratio)

        dividends = cash_summary.get("dividends", 0)
        withholding_tax = cash_summary.get("withholding_tax", 0)
        fees = cash_summary.get("fees", 0)
        net_dividends = dividends + withholding_tax

        # === 新命名 + 真实日期 timestamp ===
        def _ymd(d):
            return str(d).replace("-", "") if d else ""

        fd = _ymd(first_date)
        ld = _ymd(last_date)

        if fd and ld:
            new_name = f"ibkr_statement({fd}-{ld}).csv"
            # 把 YYYYMMDD 转成 YYYY-MM-DD
            if len(ld) == 8 and ld.isdigit():
                timestamp = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
            else:
                timestamp = str(last_date)
        else:
            new_name = f
            timestamp = f.replace("ibkr_flex_", "").replace(".csv", "")

        previous_deposit = 0
        if not history_df.empty:
            if "Platform" in history_df.columns:
                ibkr_only = history_df[history_df["Platform"] == "IBKR"]
            else:
                ibkr_only = history_df
            if len(ibkr_only) > 0 and "TotalDeposit" in ibkr_only.columns:
                previous_deposit = ibkr_only.iloc[-1]["TotalDeposit"]

        new_row = pd.DataFrame([{
            "Platform": "IBKR",
            "Timestamp": timestamp,
            "SnapshotFile": new_name,
            "NAV": nav,
            "Cash": cash,
            "PnL": pnl,
            "TotalDeposit": previous_deposit + deposit,
            "PeriodDeposit": deposit,
            "Dividends": dividends,
            "WithholdingTax": withholding_tax,
            "NetDividends": net_dividends,
            "Fees": fees,
            "UsdToSgd": fx_ratio,
        }])

        history_df = pd.concat([history_df, new_row], ignore_index=True)

        snapshot_path = os.path.join(IBKR_SNAPSHOT_DIR, new_name)
        os.rename(incoming_path, snapshot_path)

    if "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["Platform", "SnapshotFile"], keep="last"
        )

    history_df.to_csv(HISTORY_FILE, index=False)

# ============================================================
# ANALYZE POSITIONS (uses PositionValueSgd directly)
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
        symbol = str(row.get("Symbol", ""))
        asset_class = str(row.get("AssetClass", ""))

        # Use SGD value directly
        position_value_signed = _safe_float(row.get("PositionValueSgd", 0))
        position_value_abs = abs(position_value_signed)

        if asset_class == "OPT":
            option_total_signed += position_value_signed
            option_total_exposure += position_value_abs

            quantity = _safe_float(row.get("Quantity", 0))
            put_call = str(row.get("Put/Call", "")).strip().upper()
            underlying = str(row.get("UnderlyingSymbol", "")).strip()
            strike = row.get("Strike", "")
            expiry_str = str(row.get("Expiry", "")).strip()
            days_to_expiry = row.get("DTE", None)

            if days_to_expiry is None or pd.isna(days_to_expiry):
                days_to_expiry = _calc_dte(expiry_str)

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
# TRADES PARSER (Unified Schema)
# ============================================================

def parse_trades(file_obj, fx_ratio=None):
    if fx_ratio is None:
        file_obj.seek(0)
        fx_ratio = _compute_fx_ratio(file_obj)

    file_obj.seek(0)
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    trades_section = False
    header = None
    rows = []

    for line in lines:
        if (
            not trades_section
            and '"Symbol"' in line
            and '"Buy/Sell"' in line
            and '"TradePrice"' in line
        ):
            trades_section = True
            header = line
            continue

        if trades_section and line.strip() == "":
            break
        if trades_section and '"Header"' in line:
            break
        if trades_section and '"Type"' in line and '"Description"' in line:
            break

        if trades_section:
            parts = line.replace('"', '').split(",")
            if len(parts) > 2 and parts[0].strip() not in ("Total", "SubTotal", ""):
                rows.append(line)

    if header is None or len(rows) == 0:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    csv_text = header + "\n" + "\n".join(rows)

    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    if "AssetClass" in df.columns:
        df = df[df["AssetClass"].isin(["STK", "OPT"])]

    if df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    trades = []
    for _, row in df.iterrows():
        trade_date = str(row.get("TradeDate", "")) if pd.notna(row.get("TradeDate", "")) else ""
        symbol = str(row.get("Symbol", ""))
        description = str(row.get("Description", "")) if pd.notna(row.get("Description", "")) else ""
        asset_class = str(row.get("AssetClass", ""))
        buy_sell = str(row.get("Buy/Sell", "")).strip().upper()
        quantity = _safe_float(row.get("Quantity", 0))
        trade_price = _safe_float(row.get("TradePrice", 0))
        currency = str(row.get("CurrencyPrimary", "USD")) if pd.notna(row.get("CurrencyPrimary", "")) else "USD"
        net_cash = _safe_float(row.get("NetCash", 0))
        commission = _safe_float(row.get("IBCommission", 0))
        realized_pnl = _safe_float(row.get("FifoPnlRealized", 0))
        realized_pnl_sgd = realized_pnl * fx_ratio

        trades.append({
            "Platform": "IBKR",
            "TradeDate": trade_date,
            "Symbol": symbol,
            "Description": description,
            "AssetClass": asset_class,
            "Buy/Sell": buy_sell,
            "Quantity": quantity,
            "TradePrice": trade_price,
            "Currency": currency,
            "NetCash": net_cash,
            "Commission": commission,
            "RealizedPnL": realized_pnl,
            "RealizedPnLSgd": realized_pnl_sgd,
            "UsdToSgd": fx_ratio,
        })

    result = pd.DataFrame(trades)
    if result.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    keep = [c for c in UNIFIED_TRADES_COLS if c in result.columns]
    return result[keep]


def save_trades_history(file_obj, fx_ratio=None):
    file_obj.seek(0)
    if fx_ratio is None:
        fx_ratio = _compute_fx_ratio(file_obj)
        file_obj.seek(0)

    new_trades = parse_trades(file_obj, fx_ratio=fx_ratio)

    if new_trades.empty:
        return load_trades_history()

    key_cols = []
    for col in ["TradeDate", "Symbol", "Buy/Sell", "Quantity", "TradePrice"]:
        if col in new_trades.columns:
            key_cols.append(col)

    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            existing = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
        except:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    new_trades_str = new_trades.astype(str).replace("nan", "")

    if not existing.empty and len(key_cols) > 0:
        combined = pd.concat([existing, new_trades_str], ignore_index=True)
        combined = combined.drop_duplicates(subset=key_cols, keep="first")
    else:
        combined = pd.concat([existing, new_trades_str], ignore_index=True)

    if "TradeDate" in combined.columns:
        combined = combined.sort_values("TradeDate", ascending=False)

    combined.to_csv(TRADES_HISTORY_FILE, index=False)
    return combined


def load_trades_history():
    if not os.path.exists(TRADES_HISTORY_FILE):
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    try:
        return pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)


# ============================================================
# CASH SUMMARY (raw IBKR cash transactions parse — SGD values)
# ============================================================

def parse_cash_summary(file_obj):
    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    cash_section = False
    rows = []

    for line in lines:
        if '"CurrencyPrimary","AssetClass","Date/Time","Amount","Type","Description"' in line:
            cash_section = True
            continue
        if cash_section and line.startswith('"Description"'):
            break
        if cash_section:
            rows.append(line)

    if len(rows) == 0:
        return {"dividends": 0, "withholding_tax": 0, "fees": 0, "deposits": 0}

    csv_text = "\n".join(rows)
    columns = ["Currency", "AssetClass", "DateTime", "Amount", "Type", "Description"]

    try:
        df = pd.read_csv(io.StringIO(csv_text), names=columns)
    except:
        return {"dividends": 0, "withholding_tax": 0, "fees": 0, "deposits": 0}

    dividends = 0
    withholding_tax = 0
    fees = 0
    deposits = 0

    for _, row in df.iterrows():
        try:
            amount = float(row["Amount"])
        except:
            continue

        type_str = str(row.get("Type", "")).strip()
        desc = str(row.get("Description", "")).strip()

        if "CASH" in desc and ("USD.SGD" in desc or "SGD.USD" in desc):
            continue

        if type_str == "Dividends":
            dividends += amount
        elif type_str == "Withholding Tax":
            withholding_tax += amount
        elif type_str == "Deposits/Withdrawals":
            if amount > 0:
                deposits += amount
        elif type_str == "Other Fees" or "Fee" in desc:
            fees += amount

    return {
        "dividends": dividends,
        "withholding_tax": withholding_tax,
        "fees": fees,
        "deposits": deposits,
    }


# ============================================================
# CASH SUMMARY TOTAL (from HISTORY_FILE, IBKR only)
# ============================================================

def load_cash_summary_total():
    if not os.path.exists(HISTORY_FILE):
        return {"dividends": 0, "withholding_tax": 0, "net_dividends": 0, "fees": 0, "deposits": 0}

    try:
        df = pd.read_csv(HISTORY_FILE)
    except:
        return {"dividends": 0, "withholding_tax": 0, "net_dividends": 0, "fees": 0, "deposits": 0}

    if df.empty:
        return {"dividends": 0, "withholding_tax": 0, "net_dividends": 0, "fees": 0, "deposits": 0}

    if "Platform" in df.columns:
        df = df[df["Platform"] == "IBKR"]

    if df.empty:
        return {"dividends": 0, "withholding_tax": 0, "net_dividends": 0, "fees": 0, "deposits": 0}

    for col in ["Dividends", "WithholdingTax", "NetDividends", "Fees", "PeriodDeposit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    total_deposit = 0
    if "TotalDeposit" in df.columns:
        td = pd.to_numeric(df["TotalDeposit"], errors="coerce").fillna(0)
        if len(td) > 0:
            total_deposit = float(td.iloc[-1])

    return {
        "dividends": float(df["Dividends"].sum()) if "Dividends" in df.columns else 0,
        "withholding_tax": float(df["WithholdingTax"].sum()) if "WithholdingTax" in df.columns else 0,
        "net_dividends": float(df["NetDividends"].sum()) if "NetDividends" in df.columns else 0,
        "fees": float(df["Fees"].sum()) if "Fees" in df.columns else 0,
        "deposits": total_deposit,
    }


# ============================================================
# REALIZED PNL SUMMARY (in SGD)
# ============================================================

def load_realized_pnl_summary():
    trades_df = load_trades_history()

    if trades_df.empty:
        return {"realized_profit": 0, "realized_loss": 0, "realized_net": 0}

    # Prefer SGD column
    if "RealizedPnLSgd" in trades_df.columns:
        col = "RealizedPnLSgd"
    elif "RealizedPnL" in trades_df.columns:
        col = "RealizedPnL"
    elif "FifoPnlRealized" in trades_df.columns:
        col = "FifoPnlRealized"
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