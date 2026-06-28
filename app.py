import streamlit as st
import os
import re
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📊",
    layout="wide"
)

# ============================================================
# Constants
# ============================================================
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Welcome#123")

if os.path.exists("/mnt/data"):
    DATA_DIR = "/mnt/data"
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
HISTORY_FILE = os.path.join(DATA_DIR, "portfolio_history.csv")
INCOMING_DIR = os.path.join(DATA_DIR, "incoming")

os.makedirs(SNAPSHOT_DIR, exist_ok=True)
os.makedirs(INCOMING_DIR, exist_ok=True)


# ============================================================
# UNIFIED CONSTANTS (shared by IBKR / Tiger / Moomoo)
# ============================================================

TRADES_HISTORY_FILE = os.path.join(DATA_DIR, "trades_history.csv")

INDEX_ETFS = ["CSPX", "VOO", "VT", "QQQ", "QQQM", "BNDW", "SPY", "DIA", "IWM"]

TARGET_ETF_STOCK_TOTAL = 60
TARGET_SINGLE_STOCK = 10
TARGET_OPTION_TOTAL = 20
TARGET_CASH = 20

OPTION_TARGETS = {
    "Sell Put": 40, "Sell Call": 40, "LEAPS Call": 20,
    "Long Call": 10, "Long Put": 10, "Other Options": 0
}

OPTION_COLORS = {
    "Sell Put": "#4A7BFF", "Sell Call": "#00D4FF", "LEAPS Call": "#FFC300",
    "Long Call": "#00D4AA", "Long Put": "#FF6666", "Other Options": "#9CA3AF"
}

OPTION_MULTIPLIER = 100

DEFAULT_USDSGD = 1.34
DEFAULT_HKDSGD = 0.17


# ============================================================
# UNIFIED SCHEMA
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
    "Strategy", "Notes",
    "NetCash", "Commission",
    "RealizedPnL", "RealizedPnLSgd", "UsdToSgd",
]

JOURNAL_COLS = ["Strategy", "Notes"]

# ⭐ Withdrawals stored as NEGATIVE (e.g. -500). Net Capital = Deposits + Withdrawals.
# ⭐ Other = misc cashflow not categorized as Deposit/Withdrawal/Dividend/Tax/Fee
#    (e.g. Moomoo's Stock Yield Income, MM Fund movements, currency exchange)
UNIFIED_HISTORY_COLS = [
    "Platform", "Timestamp", "SnapshotFile",
    "NAV", "Cash", "PnL",
    "TotalDeposit", "PeriodDeposit",
    "TotalWithdrawal", "PeriodWithdrawal",
    "TotalOther", "PeriodOther",
    "Dividends", "WithholdingTax", "NetDividends", "Fees",
    "UsdToSgd",
]


# ============================================================
# PASSWORD — 全屏覆盖
# ============================================================
def check_password():
    """只在 app.py 调用，全屏密码页面"""
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none; }
    header { display: none; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("<div style='height:20vh'></div>", unsafe_allow_html=True)
        st.markdown("## 📊 Portfolio Dashboard")
        st.markdown("---")
        password = st.text_input("🔒 请输入密码", type="password")

        if password == "":
            st.stop()

        if password == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误")
            st.stop()


def require_auth():
    """子页面调用 — 未登录时显示密码框（不再要求跳 app 页）"""
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none; }
    header { display: none; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.markdown("<div style='height:20vh'></div>", unsafe_allow_html=True)
        st.markdown("## 📊 Portfolio Dashboard")
        st.markdown("---")
        password = st.text_input("🔒 请输入密码", type="password", key="auth_pw_input")

        if password == "":
            st.stop()

        if password == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("密码错误")
            st.stop()


# ============================================================
# CSS
# ============================================================
def load_css():
    st.markdown("""<style>

    /* 隐藏 sidebar 里的 app 入口 */
    [data-testid="stSidebarNav"] > ul > li:first-child {
        display: none;
    }

    .main {
        background-color: #F5F7FB;
    }

    .card {
        background-color: #111827;
        padding: 24px;
        border-radius: 16px;
        margin-bottom: 24px;
    }

    .big-number {
        font-size: 42px;
        font-weight: bold;
        color: white;
    }

    .green {
        color: #66FF99;
        font-size: 24px;
        font-weight: bold;
    }

    .red {
        color: #FF6666;
        font-size: 24px;
        font-weight: bold;
    }

    .section-title {
        font-size: 28px;
        font-weight: bold;
        color: black;
        margin-top: 24px;
        margin-bottom: 20px;
    }

    .progress-container {
        width: 100%;
        background-color: #1B2435;
        border-radius: 10px;
        height: 16px;
        margin-top: 6px;
        margin-bottom: 20px;
    }

    .progress-bar {
        height: 16px;
        border-radius: 10px;
    }

    .metric-title {
        color: black;
        font-size: 16px;
        font-weight: bold;
    }

    .metric-sub {
        color: #666666;
        font-size: 13px;
    }

    /* ============================================================
       响应式：手机模式
       ============================================================ */
    @media (max-width: 640px) {

        .card {
            padding: 16px !important;
            border-radius: 12px !important;
            margin-bottom: 16px !important;
        }

        .big-number {
            font-size: 28px !important;
        }

        .section-title {
            font-size: 20px !important;
            margin-top: 16px !important;
            margin-bottom: 12px !important;
        }

        .green, .red {
            font-size: 18px !important;
        }

        /* 让所有内联 grid 卡片在手机自动缩小 minmax */
        div[style*="grid-template-columns"] {
            gap: 14px !important;
        }

        /* flex 内的 span 在手机不会撑爆 */
        div[style*="display:flex"] > span {
            word-break: break-word;
        }
    }

    /* ============================================================
       响应式：平板模式
       ============================================================ */
    @media (min-width: 641px) and (max-width: 1024px) {

        .big-number {
            font-size: 34px !important;
        }

        .section-title {
            font-size: 24px !important;
        }
    }

    </style>""", unsafe_allow_html=True)


# ============================================================
# Helper: 格式化 dataframe 显示
# ============================================================
def format_df(df, cols_2dp=None, cols_3dp=None, date_cols=None):
    """
    Format dataframe for display.
    - cols_2dp: round to 2 decimals
    - cols_3dp: round to 3 decimals
    - date_cols: normalize date columns (handles YYYYMMDD and "2026-05-26 09:31:33,")
    """
    out = df.copy()

    if date_cols:
        if isinstance(date_cols, str):
            date_cols = [date_cols]
        for c in date_cols:
            if c in out.columns:
                def _fmt(s):
                    s = str(s).strip().rstrip(",").strip()
                    if len(s) == 8 and s.isdigit():
                        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                    if " " in s:
                        s = s.split(" ")[0]
                    return s
                out[c] = out[c].apply(_fmt)

    if cols_2dp:
        for c in cols_2dp:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce").round(2)

    if cols_3dp:
        for c in cols_3dp:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce").round(3)

    return out


# ============================================================
# Detect Platform
# ============================================================
def detect_platform(file_bytes):
    """
    自动识别 CSV 来自哪个 broker。
    Return: "IBKR" / "Tiger" / "Moomoo" / None
    """
    if isinstance(file_bytes, str):
        file_bytes = file_bytes.encode("utf-8")

    # ---- Moomoo 检测 ----
    if b"Moomoo Statement" in file_bytes:
        return "Moomoo"

    # ---- Tiger 检测 ----
    if b"Tiger Brokers" in file_bytes or b"Activity Statement" in file_bytes:
        if b"Account Overview" in file_bytes and b"Holdings" in file_bytes:
            return "Tiger"

    # ---- IBKR 检测 ----
    if b'"AssetClass"' in file_bytes or b"ClientAccountID" in file_bytes:
        return "IBKR"

    return None


# ============================================================
# COVERAGE / GAP DETECTION
# (Reads from portfolio_history.csv SnapshotFile names)
# Shared by IBKR / Tiger / Moomoo pages
# ============================================================

def _extract_dates_from_filename(snapshot_file):
    """
    Extract (start_dt, end_dt) from filenames like:
      moomoo_statement(20250627-20260627).csv
      ibkr_statement(20250101-20251231).csv
      tiger_statement(20250101-20251231).csv
    """
    s = str(snapshot_file)
    m = re.search(r"\((\d{8})\s*-\s*(\d{8})\)", s)
    if not m:
        return None, None

    try:
        sd = datetime.strptime(m.group(1), "%Y%m%d")
        ed = datetime.strptime(m.group(2), "%Y%m%d")
        return sd, ed
    except:
        return None, None


def detect_coverage_gaps(platform):
    """
    Detect gaps in statement coverage by reading SnapshotFile names
    from portfolio_history.csv.

    Returns:
      {
        "ranges":   [(start, end), ...],
        "gaps":     [(gap_start, gap_end), ...],
        "overlaps": [(a, b), ...],
        "total_days":   int,
        "covered_days": int,
      }
    """
    result = {
        "ranges": [],
        "gaps": [],
        "overlaps": [],
        "total_days": 0,
        "covered_days": 0,
    }

    if not os.path.exists(HISTORY_FILE):
        return result

    try:
        df = pd.read_csv(HISTORY_FILE)
    except:
        return result

    if df.empty or "Platform" not in df.columns or "SnapshotFile" not in df.columns:
        return result

    df = df[df["Platform"] == platform]
    if df.empty:
        return result

    ranges = []
    for _, row in df.iterrows():
        sd, ed = _extract_dates_from_filename(row["SnapshotFile"])
        if sd is not None and ed is not None:
            ranges.append((sd, ed))

    if not ranges:
        return result

    ranges.sort(key=lambda x: x[0])
    result["ranges"] = [(s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")) for s, e in ranges]

    # Overlaps
    for i in range(1, len(ranges)):
        prev_s, prev_e = ranges[i - 1]
        cur_s, cur_e = ranges[i]
        if cur_s <= prev_e:
            result["overlaps"].append(
                (cur_s.strftime("%Y-%m-%d"), min(prev_e, cur_e).strftime("%Y-%m-%d"))
            )

    # Merge ranges
    merged = []
    for s, e in ranges:
        if not merged:
            merged.append([s, e])
        else:
            last_s, last_e = merged[-1]
            if s <= last_e + timedelta(days=1):
                merged[-1][1] = max(last_e, e)
            else:
                merged.append([s, e])

    # Gaps between merged ranges
    for i in range(1, len(merged)):
        gap_start = merged[i - 1][1] + timedelta(days=1)
        gap_end = merged[i][0] - timedelta(days=1)
        if gap_start <= gap_end:
            result["gaps"].append(
                (gap_start.strftime("%Y-%m-%d"), gap_end.strftime("%Y-%m-%d"))
            )

    overall_start = merged[0][0]
    overall_end = merged[-1][1]
    total_days = (overall_end - overall_start).days + 1

    covered_days = 0
    for s, e in merged:
        covered_days += (e - s).days + 1

    result["total_days"] = total_days
    result["covered_days"] = covered_days

    return result


def get_coverage_summary(platform):
    """Human-readable summary for dashboard display."""
    info = detect_coverage_gaps(platform)
    lines = []

    if not info["ranges"]:
        return f"⚠️ 没有 {platform} statement coverage 记录。"

    lines.append(f"📅 {platform} 已上传 {len(info['ranges'])} 份 statement")
    lines.append(f"   覆盖 {info['covered_days']} / {info['total_days']} 天")

    if info["gaps"]:
        lines.append(f"\n⚠️ 检测到 {len(info['gaps'])} 个日期缺口：")
        for gs, ge in info["gaps"]:
            lines.append(f"   • {gs} → {ge}")
        lines.append("\n👉 建议补一份覆盖这段时间的 statement，否则 FIFO 可能不准。")
    else:
        lines.append("✅ 没有日期缺口，FIFO 计算可信。")

    if info["overlaps"]:
        lines.append(f"\nℹ️ 有 {len(info['overlaps'])} 个重叠区间（不影响，已去重）：")
        for os_, oe in info["overlaps"][:5]:
            lines.append(f"   • {os_} → {oe}")

    return "\n".join(lines)


# ============================================================
# 主入口 — 登录后跳转 Overview
# ============================================================
# 只有直接跑 app.py 时才执行，被 import 时不执行
if __name__ == "__main__":
    check_password()
    st.switch_page("pages/1_Overview.py")