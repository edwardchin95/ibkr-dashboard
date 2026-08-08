import streamlit as st
import os
import json
import re
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📊",
    layout="wide"
)

# ============================================================
# Market Calendars (NYSE + SGX) — for gap detection
# ============================================================
try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar("NYSE")
    _SGX = mcal.get_calendar("SGX")
    _HAS_MCAL = True
except Exception:
    _NYSE = None
    _SGX = None
    _HAS_MCAL = False


def _get_open_trading_days(start_date, end_date):
    """
    Return a set of dates where at least ONE market
    (NYSE or SGX) is open in the given range.

    Fallback (if pandas_market_calendars not installed):
    treat all weekdays as trading days.
    """

    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()

    if _HAS_MCAL:

        nyse_days = set(
            _NYSE.schedule(
                start_date=start,
                end_date=end
            ).index.date
        )

        sgx_days = set(
            _SGX.schedule(
                start_date=start,
                end_date=end
            ).index.date
        )

        return nyse_days | sgx_days

    # Fallback: use weekdays only
    days = set()
    current = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)

    while current <= end_dt:
        if current.weekday() < 5:  # Mon–Fri
            days.add(current.date())
        current += pd.Timedelta(days=1)

    return days


def _gap_has_trading_day(start_date, end_date):
    """
    True if the date range contains at least 1 day
    where NYSE or SGX is open.
    """
    return len(
        _get_open_trading_days(start_date, end_date)
    ) > 0


# ============================================================
# Constants
# ============================================================
ALLOWED_USERS = [
    "garvill1230@gmail.com",
]

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

# One real account, multiple accepted usernames/aliases
LOCAL_ACCOUNT = {
    "usernames": ["edward", "garvill1230@gmail.com"],
    "password": DASHBOARD_PASSWORD,
    "user_id": "garvill1230@gmail.com",
}

if os.path.exists("/mnt/data"):
    DATA_DIR = "/mnt/data"
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


# ============================================================
# USER ISOLATION
# ============================================================

def get_current_user():
    """
    Temporary.
    Later this will come from Google OAuth.
    """
    return st.session_state.get("user_id", "edward")


def get_user_dir():
    return os.path.join(
        DATA_DIR,
        "users",
        get_current_user()
    )


def get_snapshot_dir():
    return os.path.join(
        get_user_dir(),
        "snapshots"
    )


def get_incoming_dir():
    return os.path.join(
        get_user_dir(),
        "incoming"
    )


def get_history_file():
    return os.path.join(
        get_user_dir(),
        "portfolio_history.csv"
    )


def get_trades_history_file():
    return os.path.join(
        get_user_dir(),
        "trades_history.csv"
    )


def ensure_user_workspace():

    os.makedirs(get_user_dir(), exist_ok=True)

    os.makedirs(
        get_snapshot_dir(),
        exist_ok=True
    )

    os.makedirs(
        get_incoming_dir(),
        exist_ok=True
    )

def get_settings_file():
    return os.path.join(get_user_dir(), "settings.json")


def load_setting(key, default=None):
    path = get_settings_file()
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get(key, default)
    except:
        return default


def save_setting(key, value):
    path = get_settings_file()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except:
            data = {}
    data[key] = value
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except:
        pass
# ============================================================
# UNIFIED CONSTANTS (shared by IBKR / Tiger / Moomoo)
# ============================================================



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
    "Platform", "TradeDate", "SnapshotFile", "Symbol", "Description", "AssetClass",
    "Buy/Sell", "Quantity", "TradePrice", "Currency",
    "Strategy", "Group", "Notes", "Breakeven",
    "NetCash", "Commission",
    "RealizedPnL", "RealizedPnLSgd", "UsdToSgd",
]

JOURNAL_COLS = ["Strategy", "Group", "Notes", "Breakeven"] 

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


def require_auth():
    """子页面调用 — 未登录时显示登录选项（Google 或本地账号）"""

    # Already authenticated this session
    if st.session_state.get("authenticated", False):
        ensure_user_workspace()
        render_logout_sidebar()
        return True

    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none; }
        header { display: none; }

        .gsi-material-button {
            -webkit-appearance: none;
            background-color: WHITE;
            border: 1px solid #747775;
            border-radius: 4px;
            box-sizing: border-box;
            color: #1f1f1f;
            cursor: pointer;
            font-family: 'Roboto', arial, sans-serif;
            font-size: 14px;
            height: 40px;
            letter-spacing: 0.25px;
            outline: none;
            overflow: hidden;
            padding: 0 12px;
            position: relative;
            text-align: center;
            transition: background-color .218s, border-color .218s, box-shadow .218s;
            vertical-align: middle;
            white-space: nowrap;
            width: 100%;
            text-decoration: none;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .gsi-material-button:hover {
            box-shadow: 0 1px 2px 0 rgba(60,64,67,.30), 0 1px 3px 1px rgba(60,64,67,.15);
        }
        .gsi-material-button-icon {
            height: 20px;
            width: 20px;
            margin-right: 10px;
        }
        .gsi-material-button-contents {
            font-weight: 500;
            color: #1f1f1f;
        }

        .or-divider {
            display: flex;
            align-items: center;
            text-align: center;
            color: #9ca3af;
            font-size: 13px;
            margin: 18px 0;
        }
        .or-divider::before, .or-divider::after {
            content: "";
            flex: 1;
            border-bottom: 1px solid #e5e7eb;
        }
        .or-divider:not(:empty)::before { margin-right: .75em; }
        .or-divider:not(:empty)::after { margin-left: .75em; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1, 1])

    with col2:
        st.markdown("<div style='height:15vh'></div>", unsafe_allow_html=True)
        st.markdown("## 📊 Portfolio Dashboard")
        st.markdown("---")

        st.markdown("#### Sign in")
        st.caption("Get instant access")

        google_html = (
            '<a href="?login=true" target="_self" style="text-decoration:none;">'
            '<button class="gsi-material-button" type="button">'
            '<div class="gsi-material-button-icon">'
            '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" style="display:block;">'
            '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"></path>'
            '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"></path>'
            '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"></path>'
            '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"></path>'
            '<path fill="none" d="M0 0h48v48H0z"></path>'
            '</svg>'
            '</div>'
            '<span class="gsi-material-button-contents">Sign in with Google</span>'
            '</button>'
            '</a>'
        )
        st.markdown(google_html, unsafe_allow_html=True)

        if st.query_params.get("login") == "true" and not st.user.is_logged_in:
            st.login()

        st.markdown('<div class="or-divider">or</div>', unsafe_allow_html=True)

        username = st.text_input(
            "Email or Username", key="local_username", label_visibility="collapsed",
            placeholder="Email or Username",
        )
        password = st.text_input(
            "Password", type="password", key="local_password", label_visibility="collapsed",
            placeholder="Password",
        )

        if st.button("Sign in", width="stretch", key="local_login_btn", type="primary"):
            if username.lower() in LOCAL_ACCOUNT["usernames"] and password == LOCAL_ACCOUNT["password"]:
                st.session_state["authenticated"] = True
                st.session_state["user_id"] = LOCAL_ACCOUNT["user_id"]
                ensure_user_workspace()
                st.rerun()
            else:
                st.error("Invalid username or password")

    # ------------------------------------------------------------
    # Handle Google login completing (user just came back from OAuth)
    # ------------------------------------------------------------
    if st.user.is_logged_in:
        email = st.user.email.lower()

        if email not in ALLOWED_USERS:
            st.error(f"You are not authorised for beta access: {email}")
            st.stop()

        st.session_state["authenticated"] = True
        st.session_state["user_id"] = email
        ensure_user_workspace()
        st.rerun()

    st.stop()


    # st.user.is_logged_in is True at this point
    email = st.user.email.lower()

    if email not in ALLOWED_USERS:
        st.error(f"🚫 {email} is not authorised for beta access.")
        st.stop()

    st.session_state["authenticated"] = True
    st.session_state["user_id"] = email

    ensure_user_workspace()

    return True

def render_logout_sidebar():
    """Call this on every authenticated page to show user + logout button."""
    with st.sidebar:
        st.markdown("---")
        st.caption(f"Logged in as: **{st.session_state.get('user_id', 'unknown')}**")
        if st.button("Log out", width="stretch"):
            if st.user.is_logged_in:
                st.logout()
            st.session_state["authenticated"] = False
            st.session_state.pop("user_id", None)
            st.rerun()
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

    # ⭐ Coerce leftover mixed-type object columns (e.g. Strike: float + "")
    # to string so Streamlit/Arrow can serialize without warnings.
    numeric_cols = set((cols_2dp or []) + (cols_3dp or []))
    for c in out.columns:
        if c in numeric_cols:
            continue
        if out[c].dtype == "object":
            out[c] = out[c].astype(str).replace(
                {"nan": "", "None": "", "NaT": ""}
            )

    return out


# ============================================================
# Detect Platform
# ============================================================
def detect_platform(file_bytes):
    """
    Auto-detect which broker/format the upload is.
    Return: "IBKR" / "Tiger" / "TigerPDF" / "Moomoo" / None
    """
    if isinstance(file_bytes, str):
        file_bytes = file_bytes.encode("utf-8")

    # ---- PDF detection (Tiger mobile/web PDF) ----
    # PDF text is compressed, so we can't byte-match "Tiger Brokers" on raw
    # bytes. Use pdfplumber (via tiger_pdf.detect_tiger_pdf) to read the text.
    if file_bytes[:5].startswith(b"%PDF"):
        try:
            from tiger_pdf import detect_tiger_pdf
            if detect_tiger_pdf(file_bytes):
                return "TigerPDF"
        except Exception:
            pass
        return None   # unknown / unreadable PDF


    # ---- Moomoo ----
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

def delete_snapshot(platform, snapshot_name):
    """
    Shared delete used by Overview + Snapshot Manager.
    Returns (ok: bool, error: str).

    Safety:
      1. Pre-checks both CSVs are writable (catches Excel/file locks locally).
      2. Writes CSVs FIRST, deletes the physical file LAST — so a mid-way
         failure never leaves an orphan row pointing at a missing file.
    """
    hist = get_history_file()
    trades = get_trades_history_file()

    # --- 1. Pre-check writability (catches "file open in Excel") ---
    for path in [hist, trades]:
        if os.path.exists(path):
            try:
                with open(path, "a"):
                    pass
            except PermissionError:
                return (False,
                        f"'{os.path.basename(path)}' is open in another program "
                        f"(e.g. Excel). Close it and try again.")
            except Exception as e:
                return (False, f"Cannot access '{os.path.basename(path)}': {e}")

    # --- 2. Update portfolio_history.csv ---
    try:
        if os.path.exists(hist):
            hdf = pd.read_csv(hist)
            if not hdf.empty and "Platform" in hdf.columns and "SnapshotFile" in hdf.columns:
                mask = ~(
                    (hdf["Platform"].astype(str) == platform)
                    & (hdf["SnapshotFile"].astype(str) == snapshot_name)
                )
                hdf[mask].to_csv(hist, index=False)
    except Exception as e:
        return (False, f"Failed to update portfolio_history.csv: {e}")

    # --- 3. Update trades_history.csv ---
    try:
        if os.path.exists(trades):
            tdf = pd.read_csv(trades, dtype=str)
            if not tdf.empty and "Platform" in tdf.columns and "SnapshotFile" in tdf.columns:
                mask = ~(
                    (tdf["Platform"].astype(str) == platform)
                    & (tdf["SnapshotFile"].astype(str) == snapshot_name)
                )
                tdf[mask].to_csv(trades, index=False)
    except Exception as e:
        return (False, f"Failed to update trades_history.csv: {e}")

    # --- 4. Delete the physical snapshot file LAST ---
    try:
        if platform == "IBKR":
            from ibkr import get_ibkr_snapshot_dir as _dirfn
        elif platform == "Tiger":
            from tiger import get_tiger_snapshot_dir as _dirfn
        elif platform == "Moomoo":
            from moomoo import get_moomoo_snapshot_dir as _dirfn
        else:
            _dirfn = None

        if _dirfn is not None:
            snap_path = os.path.join(_dirfn(), snapshot_name)
            if os.path.exists(snap_path):
                os.remove(snap_path)
    except Exception as e:
        return (False, f"History updated, but couldn't remove file: {e}")

    return (True, "")

def _existing_snapshot_files(platform):
    if not os.path.exists(get_history_file()):
        return set()
    try:
        df = pd.read_csv(get_history_file())
    except:
        return set()
    if df.empty or "Platform" not in df.columns or "SnapshotFile" not in df.columns:
        return set()
    sub = df[df["Platform"].astype(str) == platform]
    return set(sub["SnapshotFile"].astype(str))


def _find_overlap(new_snapshot_name, other_files):
    ns, ne = _extract_dates_from_filename(new_snapshot_name)
    if ns is None or ne is None:
        return None
    for other in other_files:
        if other == new_snapshot_name:
            continue
        os_, oe_ = _extract_dates_from_filename(other)
        if os_ is None or oe_ is None:
            continue
        overlap_days = (min(ne, oe_) - max(ns, os_)).days + 1
        if overlap_days >= 2:
            return other
    return None
# ============================================================
# COVERAGE / GAP DETECTION
# (Reads from portfolio_history.csv SnapshotFile names)
# Shared by IBKR / Tiger / Moomoo pages
#
# Gap filtering uses NYSE + SGX market calendars:
# a gap is only real if at least 1 trading day
# (US OR SG market open) exists in that range.
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


def _count_trading_days(start_date, end_date):
    """
    Count trading days (NYSE ∪ SGX open) between two dates inclusive.
    Fallback: count weekdays if pandas_market_calendars unavailable.
    """
    return len(
        _get_open_trading_days(start_date, end_date)
    )


def detect_coverage_gaps(platform):
    """
    Detect gaps in statement coverage by reading SnapshotFile names
    from portfolio_history.csv.

    Weekends and US/SG public holidays are excluded from
    gap detection — a gap is only reported if at least
    one trading day (NYSE OR SGX open) is missing.

    Returns:
      {
        "ranges":   [(start, end), ...],
        "gaps":     [(gap_start, gap_end), ...],
        "overlaps": [(a, b), ...],
        "total_days":   int,   # trading days
        "covered_days": int,   # trading days
      }
    """
    result = {
        "ranges": [],
        "gaps": [],
        "overlaps": [],
        "total_days": 0,
        "covered_days": 0,
    }

    if not os.path.exists(get_history_file()):
        return result

    try:
        df = pd.read_csv(get_history_file())
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
    # ⭐ Ignore boundary-day overlaps (weekly statements naturally share endpoints).
    # Only flag when overlap spans >= 2 days.
    for i in range(1, len(ranges)):
        prev_s, prev_e = ranges[i - 1]
        cur_s, cur_e = ranges[i]

        if cur_s <= prev_e:
            overlap_start = cur_s
            overlap_end = min(prev_e, cur_e)
            overlap_days = (overlap_end - overlap_start).days + 1

            # Skip single-day boundary overlaps (e.g. A ends 6/26, B starts 6/26)
            if overlap_days >= 2:
                result["overlaps"].append(
                    (overlap_start.strftime("%Y-%m-%d"),
                     overlap_end.strftime("%Y-%m-%d"))
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

    # Raw gaps between merged ranges
    raw_gaps = []
    for i in range(1, len(merged)):
        gap_start = merged[i - 1][1] + timedelta(days=1)
        gap_end = merged[i][0] - timedelta(days=1)
        if gap_start <= gap_end:
            raw_gaps.append((gap_start, gap_end))

    # ⭐ Filter: keep only gaps that contain at least 1 trading day
    # (NYSE OR SGX open). Weekend-only or holiday-only gaps are dropped.
    filtered_gaps = []
    for gs, ge in raw_gaps:
        if _gap_has_trading_day(gs, ge):
            filtered_gaps.append(
                (gs.strftime("%Y-%m-%d"), ge.strftime("%Y-%m-%d"))
            )

    result["gaps"] = filtered_gaps

    # Count coverage in TRADING DAYS (not calendar days)
    overall_start = merged[0][0]
    overall_end = merged[-1][1]

    total_days = _count_trading_days(overall_start, overall_end)

    covered_days = 0
    for s, e in merged:
        covered_days += _count_trading_days(s, e)

    result["total_days"] = total_days
    result["covered_days"] = covered_days

    return result


# ============================================================
# 主入口 — 登录后跳转 Overview
# ============================================================
if __name__ == "__main__":
    require_auth()
    st.switch_page("pages/1_Overview.py")