import pandas as pd
import io
import os
from datetime import datetime

from app import SNAPSHOT_DIR, HISTORY_FILE, INCOMING_DIR

# ============================================================
# Constants（IBKR 专属）
# ============================================================

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
# CSV PARSER
# ============================================================

def parse_ibkr_csv(file_obj):

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
        if (
            line.startswith('"STK"')
            or
            line.startswith('"OPT"')
        ):
            filtered.append(line)

    filtered.insert(0, lines[position_start])

    csv_text = "\n".join(filtered)

    df = pd.read_csv(io.StringIO(csv_text))

    return df

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

        if (
            nav_section
            and
            line.startswith('"ReportDate"')
        ):
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
# EXTRACT TOTAL PNL
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

        if (
            pnl_section
            and
            line.startswith('"AssetClass"')
        ):
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

        if (
            cash_section
            and
            line.startswith('"Description"')
        ):
            break

        if cash_section:
            rows.append(line)

    if len(rows) == 0:
        return 0

    csv_text = "\n".join(rows)

    columns = [
        "Currency",
        "AssetClass",
        "DateTime",
        "Amount",
        "Type",
        "Description"
    ]

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
# SAVE SNAPSHOT + HISTORY（合并版：含 dividends/tax/fees/period_deposit）
# ============================================================

def save_snapshot_and_history(uploaded_file, nav, cash, pnl, deposit):

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    original_name = uploaded_file.name
    name_part, ext_part = os.path.splitext(original_name)

    uploaded_file.seek(0)
    first_date, last_date = extract_report_date_range(uploaded_file)
    uploaded_file.seek(0)

    if first_date and last_date:
        snapshot_filename = f"portfolio_performance({first_date}-{last_date}){ext_part}"
    else:
        snapshot_filename = f"{name_part}_{timestamp}{ext_part}"

    snapshot_path = os.path.join(SNAPSHOT_DIR, snapshot_filename)

    with open(snapshot_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    # 解析 cash summary（dividends / withholding tax / fees）
    uploaded_file.seek(0)
    cash_summary = parse_cash_summary(uploaded_file)
    uploaded_file.seek(0)

    dividends = cash_summary.get("dividends", 0)
    withholding_tax = cash_summary.get("withholding_tax", 0)
    fees = cash_summary.get("fees", 0)
    net_dividends = dividends + withholding_tax

    # 读已有 history
    if os.path.exists(HISTORY_FILE):
        try:
            history_df = pd.read_csv(HISTORY_FILE)
        except:
            history_df = pd.DataFrame()
    else:
        history_df = pd.DataFrame()

    previous_total_deposit = 0

    if (
        len(history_df) > 0
        and
        "TotalDeposit" in history_df.columns
    ):
        previous_total_deposit = history_df.iloc[-1]["TotalDeposit"]

    cumulative_deposit = previous_total_deposit + deposit

    new_row = pd.DataFrame([{
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
    }])

    history_df = pd.concat([history_df, new_row], ignore_index=True)

    # 用 SnapshotFile 去重（同一份 CSV 重传不算两次）
    if "SnapshotFile" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["SnapshotFile"], keep="last"
        )

    history_df.to_csv(HISTORY_FILE, index=False)

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

    if len(history_df) == 0:
        return None

    latest = history_df.iloc[-1]

    snapshot_file = latest["SnapshotFile"]
    snapshot_path = os.path.join(SNAPSHOT_DIR, snapshot_file)

    if not os.path.exists(snapshot_path):
        return None

    with open(snapshot_path, "rb") as f:
        fake_upload = io.BytesIO(f.read())
        fake_upload.name = snapshot_file

        total_nav_snap, cash_snap, stock_nav_snap, option_nav_snap = extract_nav_cash(fake_upload)

        fake_upload.seek(0)
        df_positions = parse_ibkr_csv(fake_upload)

    return {
        "df_positions": df_positions,
        "history_df": history_df,
        "nav": latest["NAV"],
        "cash": latest["Cash"],
        "stock_nav_sgd": stock_nav_snap,
        "option_nav_sgd": option_nav_snap,
        "pnl": latest["PnL"],
        "deposit": latest["TotalDeposit"]
    }

# ============================================================
# PROCESS INCOMING（合并版）
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
            fake_upload = io.BytesIO(fh.read())
            fake_upload.name = f

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

            # 保存交易记录
            fake_upload.seek(0)
            save_trades_history(fake_upload)

        dividends = cash_summary.get("dividends", 0)
        withholding_tax = cash_summary.get("withholding_tax", 0)
        fees = cash_summary.get("fees", 0)
        net_dividends = dividends + withholding_tax

        if first_date and last_date:
            new_name = f"portfolio_performance_({first_date}-{last_date}).csv"
        else:
            new_name = f

        previous_deposit = 0
        if len(history_df) > 0 and "TotalDeposit" in history_df.columns:
            previous_deposit = history_df.iloc[-1]["TotalDeposit"]

        timestamp = (
            f"{first_date}-{last_date}"
            if first_date and last_date
            else f.replace("ibkr_flex_", "").replace(".csv", "")
        )

        new_row = pd.DataFrame([{
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
        }])

        history_df = pd.concat([history_df, new_row], ignore_index=True)

        snapshot_path = os.path.join(SNAPSHOT_DIR, new_name)
        os.rename(incoming_path, snapshot_path)

    # 用 SnapshotFile 去重
    if "SnapshotFile" in history_df.columns:
        history_df = history_df.drop_duplicates(
            subset=["SnapshotFile"], keep="last"
        )

    history_df.to_csv(HISTORY_FILE, index=False)

# ============================================================
# ANALYZE POSITIONS（持仓分析逻辑）
# ============================================================

def analyze_positions(df_positions, total_nav, cash_sgd):

    index_etf_total = 0
    stock_total = 0
    stock_total_signed = 0
    option_total_signed = 0
    option_total_exposure = 0

    index_etf_positions = []
    stock_positions = []

    option_categories = {
        "Sell Put": 0,
        "Sell Call": 0,
        "LEAPS Call": 0,
        "Long Call": 0,
        "Long Put": 0,
        "Other Options": 0
    }

    option_positions = []

    fx_ratio = 1.0

    defaults = {
        "index_etf_total": 0,
        "stock_total": 0,
        "stock_total_signed": 0,
        "option_total_signed": 0,
        "option_total_exposure": 0,
        "index_etf_positions": [],
        "stock_positions": [],
        "option_categories": option_categories,
        "option_positions": [],
        "fx_ratio": 1.0,
        "stock_nav_sgd": 0,
        "option_nav_sgd": 0,
        "stock_pct_signed": 0,
        "option_pct_signed": 0,
        "option_pct_exposure": 0,
        "cash_pct": 0,
    }

    if df_positions is None:
        return defaults

    for _, row in df_positions.iterrows():

        symbol = str(row["Symbol"])
        asset_class = str(row["AssetClass"])

        try:
            position_value_signed = float(row["PositionValue"])
        except:
            position_value_signed = 0

        position_value_abs = abs(position_value_signed)

        # ---- OPTIONS ----
        if asset_class == "OPT":

            option_total_signed += position_value_signed
            option_total_exposure += position_value_abs

            try:
                quantity = float(row["Quantity"])
            except:
                quantity = 0

            put_call = str(row.get("Put/Call", "")).strip().upper()
            underlying = str(row.get("UnderlyingSymbol", "")).strip()
            strike = row.get("Strike", "")
            expiry_str = str(row.get("Expiry", "")).strip()

            days_to_expiry = None

            try:
                if expiry_str and expiry_str.lower() != "nan":
                    es = expiry_str.strip()

                    # 防止 pandas 把 260717 读成 float "260717.0"
                    if "." in es:
                        es = es.split(".")[0]

                    # 6 位 YYMMDD → 8 位 YYYYMMDD
                    if len(es) == 6:
                        es = "20" + es

                    expiry_date = datetime.strptime(es, "%Y%m%d")
                    days_to_expiry = (expiry_date - datetime.now()).days
            except:
                days_to_expiry = None

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
                "Category": category,
                "Underlying": underlying,
                "Symbol": symbol,
                "Put/Call": put_call,
                "Quantity": quantity,
                "Strike": strike,
                "Expiry": expiry_str,
                "DTE": days_to_expiry,
                "SignedValue": position_value_signed,
                "Exposure": position_value_abs
            })

        # ---- ETF ----
        elif symbol in INDEX_ETFS:

            index_etf_total += position_value_abs
            stock_total_signed += position_value_signed

            index_etf_positions.append({
                "Symbol": symbol,
                "Value": position_value_abs
            })

        # ---- STOCKS ----
        else:

            stock_total += position_value_abs
            stock_total_signed += position_value_signed

            stock_positions.append({
                "Symbol": symbol,
                "Value": position_value_abs
            })

    # ---- FX Ratio ----
    invested_position_base = stock_total_signed + option_total_signed
    invested_nav_sgd = total_nav - cash_sgd

    if invested_position_base != 0:
        fx_ratio = invested_nav_sgd / invested_position_base

    stock_nav_sgd = stock_total_signed * fx_ratio
    option_nav_sgd = option_total_signed * fx_ratio

    # ---- Percentages ----
    stock_pct_signed = (stock_nav_sgd / total_nav * 100) if total_nav != 0 else 0
    option_pct_signed = (option_nav_sgd / total_nav * 100) if total_nav != 0 else 0
    option_pct_exposure = (
        option_total_exposure * abs(fx_ratio) / total_nav * 100
    ) if total_nav != 0 else 0
    cash_pct = (cash_sgd / total_nav * 100) if total_nav != 0 else 0

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
        "fx_ratio": fx_ratio,
        "stock_nav_sgd": stock_nav_sgd,
        "option_nav_sgd": option_nav_sgd,
        "stock_pct_signed": stock_pct_signed,
        "option_pct_signed": option_pct_signed,
        "option_pct_exposure": option_pct_exposure,
        "cash_pct": cash_pct,
    }

# ============================================================
# TRADES HISTORY（累计交易记录 — 只有买卖，不含 dividend/deposit/换钱）
# ============================================================

TRADES_HISTORY_FILE = os.path.join(
    os.path.dirname(HISTORY_FILE),
    "ibkr_trades_history.csv"
)

# 交易记录只保留这些列
TRADES_DISPLAY_COLS = [
    "TradeDate",
    "Symbol",
    "Description",
    "AssetClass",
    "Buy/Sell",
    "Quantity",
    "TradePrice",
    "IBCommission",
    "NetCash",
    "FifoPnlRealized"
]

def parse_trades(file_obj):
    """从 IBKR CSV 只提取 Trades（买卖记录），排除换钱/dividend/deposit"""

    content = file_obj.getvalue().decode("utf-8")
    lines = content.splitlines()

    trades_section = False
    header = None
    rows = []

    for line in lines:

        # 找 Trades header（必须有 Buy/Sell 和 TradePrice）
        if (
            not trades_section
            and
            '"Symbol"' in line
            and
            '"Buy/Sell"' in line
            and
            '"TradePrice"' in line
        ):
            trades_section = True
            header = line
            continue

        # 空行 = section 结束
        if trades_section and line.strip() == "":
            break

        # 新 section header = 结束
        if trades_section and '"Header"' in line:
            break

        # 遇到完全不同的 section（如 Cash Transactions）= 结束
        if trades_section and '"Type"' in line and '"Description"' in line:
            break

        if trades_section:
            parts = line.replace('"', '').split(",")
            if len(parts) > 2 and parts[0].strip() not in ("Total", "SubTotal", ""):
                rows.append(line)

    if header is None or len(rows) == 0:
        return pd.DataFrame()

    csv_text = header + "\n" + "\n".join(rows)

    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except:
        return pd.DataFrame()

    # ===== 关键过滤 =====
    # 只保留 STK 和 OPT（排除 CASH 换钱）
    if "AssetClass" in df.columns:
        df = df[df["AssetClass"].isin(["STK", "OPT"])]

    # 如果不小心混入了 Cash Transactions，用 Type 列排除
    if "Type" in df.columns:
        df = df[~df["Type"].isin([
            "Withholding Tax",
            "Deposits/Withdrawals",
            "Dividends",
            "Other Fees"
        ])]

    # FifoPnlRealized: 0 改成空（开仓留空，关仓显示）
    if "FifoPnlRealized" in df.columns:
        df["FifoPnlRealized"] = df["FifoPnlRealized"].apply(
            lambda x: "" if pd.isna(x) or str(x).strip() in ("0", "0.0", "0.00") else x
        )

    # 只保留需要的列
    keep_cols = [c for c in TRADES_DISPLAY_COLS if c in df.columns]
    df = df[keep_cols]

    return df


def parse_cash_summary(file_obj):
    """从 IBKR CSV 提取 Cash Transactions 汇总（股息/税/费用/存款），无视换钱"""

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
        return {
            "dividends": 0,
            "withholding_tax": 0,
            "fees": 0,
            "deposits": 0
        }

    csv_text = "\n".join(rows)

    columns = [
        "Currency", "AssetClass", "DateTime",
        "Amount", "Type", "Description"
    ]

    try:
        df = pd.read_csv(io.StringIO(csv_text), names=columns)
    except:
        return {
            "dividends": 0,
            "withholding_tax": 0,
            "fees": 0,
            "deposits": 0
        }

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

        # 无视换钱
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
        "deposits": deposits
    }


def save_trades_history(file_obj):
    """把本次 trades 追加到累计 CSV（只有买卖记录），自动去重"""

    file_obj.seek(0)
    new_trades = parse_trades(file_obj)

    if new_trades.empty:
        return load_trades_history()

    # 去重 key
    key_cols = []
    for col in ["TradeDate", "Symbol", "Buy/Sell", "Quantity", "TradePrice"]:
        if col in new_trades.columns:
            key_cols.append(col)

    # 读已有 history
    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            existing = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
        except:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    # 统一为 str 避免类型冲突
    new_trades = new_trades.astype(str).replace("nan", "")

    # 合并去重
    if not existing.empty and len(key_cols) > 0:
        combined = pd.concat([existing, new_trades], ignore_index=True)
        combined = combined.drop_duplicates(subset=key_cols, keep="first")
    else:
        combined = pd.concat([existing, new_trades], ignore_index=True)

    # 排序
    if "TradeDate" in combined.columns:
        combined = combined.sort_values("TradeDate", ascending=False)

    combined.to_csv(TRADES_HISTORY_FILE, index=False)

    return combined


def load_trades_history():
    """加载累计交易记录"""

    if not os.path.exists(TRADES_HISTORY_FILE):
        return pd.DataFrame()

    try:
        return pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except:
        return pd.DataFrame()


# ============================================================
# CASH SUMMARY TOTAL（从 portfolio_history.csv 累计）
# ============================================================

def load_cash_summary_total():
    """直接从 HISTORY_FILE 累加 dividends / withholding tax / fees / deposits"""

    if not os.path.exists(HISTORY_FILE):
        return {
            "dividends": 0,
            "withholding_tax": 0,
            "net_dividends": 0,
            "fees": 0,
            "deposits": 0,
        }

    try:
        df = pd.read_csv(HISTORY_FILE)
    except:
        return {
            "dividends": 0,
            "withholding_tax": 0,
            "net_dividends": 0,
            "fees": 0,
            "deposits": 0,
        }

    if df.empty:
        return {
            "dividends": 0,
            "withholding_tax": 0,
            "net_dividends": 0,
            "fees": 0,
            "deposits": 0,
        }

    for col in ["Dividends", "WithholdingTax", "NetDividends", "Fees", "PeriodDeposit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Deposits 用累计 TotalDeposit 的最后一行（已经累加好了）
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
# REALIZED PNL SUMMARY（从累计 trades history 算）
# ============================================================

def load_realized_pnl_summary():
    """从 ibkr_trades_history.csv 累计 realized profit / loss"""

    trades_df = load_trades_history()

    if trades_df.empty or "FifoPnlRealized" not in trades_df.columns:
        return {
            "realized_profit": 0,
            "realized_loss": 0,
            "realized_net": 0,
        }

    pnl = pd.to_numeric(trades_df["FifoPnlRealized"], errors="coerce").fillna(0)

    realized_profit = float(pnl[pnl > 0].sum())
    realized_loss = float(pnl[pnl < 0].sum())
    realized_net = float(pnl.sum())

    return {
        "realized_profit": realized_profit,
        "realized_loss": realized_loss,
        "realized_net": realized_net,
    }