import streamlit as st
import pandas as pd
import os
import uuid
import re
import html
from datetime import datetime, date, timedelta
from collections import defaultdict

st.set_page_config(page_title="Trade Journal", page_icon="📝", layout="wide")

from app import (
    require_auth, load_css, DATA_DIR,
    TRADES_HISTORY_FILE,
)

# --- DTE anchored to US market close (16:00 ET) ---
try:
    from zoneinfo import ZoneInfo
    _MARKET_TZ = ZoneInfo("America/New_York")   # auto EDT/EST
except Exception:
    _MARKET_TZ = None

def _dte_from_expiry(exp_dt):
    """Days to 16:00 ET on expiry date. >0 left · 0 today · <0 expired N days ago."""
    if _MARKET_TZ is not None:
        close = datetime(exp_dt.year, exp_dt.month, exp_dt.day, 16, 0, tzinfo=_MARKET_TZ)
        now = datetime.now(_MARKET_TZ)
    else:
        close = datetime(exp_dt.year, exp_dt.month, exp_dt.day, 20, 0)  # ~16:00 EDT in UTC
        now = datetime.utcnow()
    secs = (close - now).total_seconds()
    return int(secs // 86400) if secs >= 0 else -int((-secs) // 86400)
# ============================================================
# AUTH + STYLING
# ============================================================
require_auth()
load_css()

st.markdown("""
<style>
[data-testid="stSidebarNav"] ul li:first-child { display: none; }

/* Black campaign cards — target by container key (theme-proof) */
div[class*="st-key-campcard_"]{
    background:#0E1117 !important;
    border:1px solid #222b38 !important;
    border-radius:10px !important;
    padding:10px 14px 14px 14px !important;   /* bottom padding so open pos isn't flush */
    margin-bottom:4px !important;
}
/* Force readable text ONLY on our own markdown blocks — NOT on buttons/widgets */
div[class*="st-key-campcard_"] [data-testid="stMarkdownContainer"] p,
div[class*="st-key-campcard_"] [data-testid="stMarkdownContainer"] span,
div[class*="st-key-campcard_"] [data-testid="stMarkdownContainer"] div{
    color:#EEE;
}
/* Compact icon buttons inside the black card — small on desktop, tap-friendly on phone */
div[class*="st-key-campcard_"] button,
div[class*="st-key-campcard_"] button *{
    background:#1a2130 !important;
    color:#EEE !important;
    border-color:#2f3a4c !important;
}
div[class*="st-key-campcard_"] button{
    border:1px solid #2f3a4c !important;
    padding: 2px 6px !important;
    min-height: 40px !important;   /* mobile-friendly tap target height */
    min-width: 44px !important;    /* mobile-friendly tap target width */
    font-size: 18px !important;    /* emoji size */
    line-height: 1 !important;
    border-radius: 6px !important;
}
div[class*="st-key-campcard_"] button:hover,
div[class*="st-key-campcard_"] button:hover *{
    background:#243044 !important;
    color:#FFF !important;
    border-color:#3d4a63 !important;
}

.naming-hint {
    background: rgba(255,195,0,0.07); border-left: 3px solid #FFC300;
    padding: 8px 12px; border-radius: 4px; margin: 6px 0;
    font-size: 12px; line-height: 1.5;
}
.naming-hint code {
    background: rgba(255,195,0,0.15); color: #FFC300;
    padding: 1px 6px; border-radius: 3px; font-size: 11px;
}

.metric-row { display: flex; flex-wrap: wrap; gap: 18px; margin: 8px 0 6px 0; }
.metric-cell { min-width: 84px; }
.metric-label { color: gray; font-size: 10px; text-transform: uppercase; letter-spacing: 0.4px; }
.metric-value { font-size: 16px; font-weight: bold; line-height: 1.3; }



@media (max-width: 768px) {
    .metric-row { flex-direction: column; gap: 4px; }
    .metric-cell {
        display: flex; justify-content: space-between;
        width: 100%; border-bottom: 1px solid #1e2530; padding: 3px 0;
    }
    .metric-label { align-self: center; }
    .metric-value { font-size: 15px; }
    .campaign-title { font-size: 16px !important; }
}
</style>
""", unsafe_allow_html=True)

st.title("📝 Trade Journal")
st.caption("Notes, plans, campaign tracking. Independent of broker imports.")


# ============================================================
# CONFIG
# ============================================================

TRADE_JOURNAL_FILE = os.path.join(DATA_DIR, "trade_journal.csv")

JOURNAL_SCHEMA = ["JournalId", "CreatedAt", "Date", "Tags", "Notes", "Breakeven"]

PARENT_STRATEGIES = {"LEAP", "LEAPS", "HOLD", "STOCK", "SHARES"}
CYCLE_STRATEGIES = {"PMCC", "CC", "COLLAR", "DIAG", "CAL", "WHEEL"}
STANDALONE_STRATEGIES = {"SP", "CSP", "SYN", "IC", "VERT", "SPREAD", "STRANGLE", "STRADDLE"}

# Which single-anchor parents can have an auto Pos BE (only when exactly 1 open leg)
BE_ELIGIBLE_PARENT = {"LEAP", "LEAPS", "HOLD", "STOCK", "SHARES"}

# Marker written into a synthetic closing leg by Mark Expired
EXPIRED_MARKER = "[EXPIRED]"


# ============================================================
# FILE INIT
# ============================================================

def _ensure_journal_file():
    if not os.path.exists(TRADE_JOURNAL_FILE):
        os.makedirs(os.path.dirname(TRADE_JOURNAL_FILE), exist_ok=True)
        pd.DataFrame(columns=JOURNAL_SCHEMA).to_csv(TRADE_JOURNAL_FILE, index=False)


_ensure_journal_file()


# ============================================================
# HELPERS — string / date basics
# ============================================================

def _clean_str(v, default=""):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return default
    return s


def _normalize_date(s):
    s = _clean_str(s)
    if not s:
        return ""
    s = s.rstrip(",.;: \t").strip()
    if " " in s:
        s = s.split(" ")[0]
    if "T" in s:
        s = s.split("T")[0]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except Exception:
            continue
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%d/%m/%Y")
    except Exception:
        pass
    return ""


def _to_dt(d):
    try:
        return datetime.strptime(d, "%d/%m/%Y")
    except Exception:
        return datetime.min


def _parse_tags(tag_str):
    s = _clean_str(tag_str)
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _join_tags(tags_list):
    seen = set()
    result = []
    for t in tags_list:
        tc = t.strip()
        if tc and tc not in seen:
            seen.add(tc)
            result.append(tc)
    return ",".join(result)


def _parse_groups(group_str):
    s = _clean_str(group_str)
    if not s:
        return []
    return [g.strip() for g in s.split(",") if g.strip()]


def _safe_html(text):
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except Exception:
        pass
    return html.escape(str(text), quote=True).replace("$", "&#36;")


def _signed_qty_row(row):
    """Shared signed-quantity: SELL negative, BUY positive, else broker sign."""
    q = pd.to_numeric(row.get("Quantity", 0), errors="coerce")
    if pd.isna(q):
        return 0.0
    q = float(q)
    side = _clean_str(row.get("Buy/Sell", "")).upper()
    if side == "SELL":
        return -abs(q)
    if side == "BUY":
        return abs(q)
    return q


# ============================================================
# HELPERS — naming / hierarchy
# ============================================================

def _extract_ticker(group_name):
    parts = group_name.split("-")
    return parts[0].upper() if parts else ""


def _get_segments(group_name):
    parts = group_name.upper().split("-")
    return parts[1:] if len(parts) > 1 else []


def _last_strategy_segment(group_name):
    segments = _get_segments(group_name)
    if segments and segments[-1].isdigit():
        segments = segments[:-1]
    return segments[-1] if segments else ""


def _is_parent_strategy(group_name):
    return _last_strategy_segment(group_name) in PARENT_STRATEGIES


def _is_cycle_strategy(group_name):
    return _last_strategy_segment(group_name) in CYCLE_STRATEGIES


def _is_standalone_strategy(group_name):
    return _last_strategy_segment(group_name) in STANDALONE_STRATEGIES


def _get_children_of(parent_group, all_group_names):
    prefix = parent_group + "-"
    return [g for g in all_group_names if g.startswith(prefix) and g != parent_group]


def _get_parent_of(group_name, all_group_names):
    best = None
    for candidate in all_group_names:
        if candidate == group_name:
            continue
        if group_name.startswith(candidate + "-") and _is_parent_strategy(candidate):
            if best is None or len(candidate) > len(best):
                best = candidate
    return best


def _extract_strike_from_description(desc):
    if not desc:
        return None
    patterns = [
        r'\b(\d+(?:\.\d+)?)\s*[CP]\b',
        r'\b(?:CALL|PUT)\s+(\d+(?:\.\d+)?)',
        r'(\d+(?:\.\d+)?)\s*(?:CALL|PUT)',
    ]
    for pat in patterns:
        m = re.search(pat, desc, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                continue
    return None


def _suggest_next_cycle_name(all_group_names, parent_group, strategy="PMCC"):
    prefix = f"{parent_group}-{strategy.upper()}-"
    max_n = 0
    for g in all_group_names:
        if g.upper().startswith(prefix.upper()):
            trailing = g.upper()[len(prefix):]
            if trailing.isdigit():
                max_n = max(max_n, int(trailing))
    return f"{prefix}{max_n + 1}"


def _short_desc(desc, symbol=""):
    d = _clean_str(desc)
    m = re.search(r'\(([^)]+)\)', d)
    if m and re.search(r'\d', m.group(1)):
        return m.group(1).strip()
    return d or symbol


# ============================================================
# HELPERS — option identity
# ============================================================

def _parse_option_from_text(text):
    s = _clean_str(text).upper()
    if not s:
        return "", "", ""
    m = re.search(r"\b(\d{8})\b.*?\b(CALL|PUT|C|P)\b\s*(\d+(?:\.\d+)?)", s)
    if m:
        right = "C" if m.group(2) in ("CALL", "C") else "P"
        return m.group(1), right, str(float(m.group(3))).rstrip("0").rstrip(".")
    m = re.search(r"\b(\d{6})\s*([CP])\s*(\d+(?:\.\d+)?)\b", s)
    if m:
        return m.group(1), m.group(2), str(float(m.group(3))).rstrip("0").rstrip(".")
    m = re.search(r"([A-Z]+)(\d{6})([CP])(\d{8})", s)
    if m:
        strike = float(m.group(4)) / 1000
        return m.group(2), m.group(3), str(strike).rstrip("0").rstrip(".")
    return "", "", ""


def _extract_underlying(row):
    us = _clean_str(row.get("UnderlyingSymbol", ""))
    if us:
        return us
    sym = _clean_str(row.get("Symbol", ""))
    asset_class = _clean_str(row.get("AssetClass", "")).upper()
    if asset_class == "OPT" and sym:
        if " " in sym:
            return sym.split()[0]
        m = re.match(r'^([A-Z]+)\d{6}[CP]\d+$', sym.upper())
        if m:
            return m.group(1)
        m = re.match(r'^([A-Z]+)', sym.upper())
        if m:
            return m.group(1)
    return sym


def _option_identity_from_row(row):
    asset_class = _clean_str(row.get("AssetClass", "")).upper()
    underlying = _extract_underlying(row)
    symbol = _clean_str(row.get("Symbol", "")).upper()
    desc = _clean_str(row.get("Description", "")).upper()
    if asset_class != "OPT":
        return f"STK|{symbol or underlying}"
    expiry = right = strike = ""
    for col in ["Expiry", "Expiration", "ExpirationDate", "OptionExpiry", "Maturity"]:
        v = _clean_str(row.get(col, ""))
        if v:
            expiry = _normalize_date(v) or v
            break
    for col in ["Right", "PutCall", "CallPut", "OptionType"]:
        v = _clean_str(row.get(col, "")).upper()
        if v:
            if v.startswith("C"):
                right = "C"
            elif v.startswith("P"):
                right = "P"
            break
    for col in ["Strike", "StrikePrice", "OptionStrike"]:
        v = _clean_str(row.get(col, ""))
        if v:
            try:
                strike = str(float(v)).rstrip("0").rstrip(".")
            except Exception:
                strike = v
            break
    p_exp, p_right, p_strike = _parse_option_from_text(f"{symbol} {desc}")
    expiry = expiry or p_exp
    right = right or p_right
    strike = strike or p_strike
    if not expiry and not right and not strike:
        return f"OPT|{underlying}|{symbol}|{desc}"
    return f"OPT|{underlying}|{expiry}|{right}|{strike}"


# ============================================================
# Smart trade formatting
# ============================================================

def _format_trade_label(row):
    asset_class = _clean_str(row.get("AssetClass", "")).upper()
    desc = _clean_str(row.get("Description", ""))
    sym = _clean_str(row.get("Symbol", ""))
    if asset_class == "OPT":
        m = re.search(r'\(([^)]+)\)', desc)
        if m:
            inner = m.group(1).strip()
            if re.search(r'\d', inner):
                return inner
        if desc:
            return desc
        return sym
    return desc or sym


def _trade_row_to_tags(row):
    tags = []
    underlying = _extract_underlying(row)
    if underlying:
        tags.append(underlying)
    for g in _parse_groups(row.get("Group", "")):
        if g == "_ignore":
            continue
        if g not in tags:
            tags.append(g)
    platform = _clean_str(row.get("Platform", ""))
    if platform and platform not in tags:
        tags.append(platform)
    return ",".join(tags)


def _trade_row_to_strategy(row):
    return _clean_str(row.get("Strategy", ""))


def _trade_row_to_notes_prefill(row):
    side = _clean_str(row.get("Buy/Sell", ""))
    label = _format_trade_label(row)
    qty = _clean_str(row.get("Quantity", ""))
    price = _clean_str(row.get("TradePrice", ""))
    try:
        net_f = float(row.get("NetCash", 0))
        net_str = f" ({'+' if net_f >= 0 else ''}${net_f:,.2f})"
    except Exception:
        net_str = ""
    return f"{side} {label} qty {qty} @ {price}{net_str}"


# ============================================================
# LOADERS
# ============================================================

def _get_journal_mtime():
    return os.path.getmtime(TRADE_JOURNAL_FILE) if os.path.exists(TRADE_JOURNAL_FILE) else 0


def _get_trades_mtime():
    return os.path.getmtime(TRADES_HISTORY_FILE) if os.path.exists(TRADES_HISTORY_FILE) else 0


@st.cache_data(ttl=300)
def _load_journal(mtime):
    if not os.path.exists(TRADE_JOURNAL_FILE):
        return pd.DataFrame(columns=JOURNAL_SCHEMA)
    try:
        df = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
    except Exception:
        return pd.DataFrame(columns=JOURNAL_SCHEMA)
    if df.empty:
        return pd.DataFrame(columns=JOURNAL_SCHEMA)
    for col in JOURNAL_SCHEMA:
        if col not in df.columns:
            df[col] = ""
    df["Date"] = df["Date"].apply(_normalize_date)
    df["_has_be"] = df["Breakeven"].fillna("").str.strip() != ""
    df = df.sort_values("_has_be", ascending=False)
    df = df.drop_duplicates(subset=["Date", "Tags", "Notes"], keep="first")
    df = df.drop(columns=["_has_be"])
    return df[JOURNAL_SCHEMA]


@st.cache_data(ttl=300)
def _load_trades(mtime):
    if not os.path.exists(TRADES_HISTORY_FILE):
        return pd.DataFrame()
    try:
        return pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except Exception:
        return pd.DataFrame()


def _save_journal(df):
    df = df[JOURNAL_SCHEMA].copy()
    df["Date"] = df["Date"].apply(_normalize_date)
    df.to_csv(TRADE_JOURNAL_FILE, index=False)


journal_df = _load_journal(_get_journal_mtime())
trades_df = _load_trades(_get_trades_mtime())


# ============================================================
# TAG / CAMPAIGN DISCOVERY
# ============================================================

def _get_all_tags(journal_df):
    tags = set()
    for tag_str in journal_df["Tags"].dropna():
        for t in _parse_tags(tag_str):
            tags.add(t)
    return sorted(tags)


ALL_TAGS = _get_all_tags(journal_df)


def _get_all_campaign_groups(journal_df, trades_df):
    groups = set()
    if not trades_df.empty and "Group" in trades_df.columns:
        for g_str in trades_df["Group"].dropna():
            for g in _parse_groups(g_str):
                if g == "_ignore":
                    continue
                groups.add(g)
    for tag_str in journal_df["Tags"].dropna():
        for t in _parse_tags(tag_str):
            if t in groups or t == "_ignore":
                continue
            if "-" in t and any(c.isdigit() for c in t):
                groups.add(t)
    return sorted(groups)


# ============================================================
# CAMPAIGN COMPUTATION
# ============================================================

def _compute_campaign(group_name, trades_df, journal_df):
    if not trades_df.empty and "Group" in trades_df.columns:
        mask = trades_df["Group"].fillna("").apply(
            lambda g: group_name in _parse_groups(g) and "_ignore" not in _parse_groups(g)
        )
        gt = trades_df[mask].copy()
    else:
        gt = pd.DataFrame()

    net_cash = 0.0
    realized = 0.0
    open_positions = []
    platforms = set()
    strategy_tags = set()

    if not gt.empty:
        def _cash_flow(row):
            net_cash_val = pd.to_numeric(row.get("NetCash", 0), errors="coerce")
            if pd.isna(net_cash_val):
                qty = pd.to_numeric(row.get("Quantity", 0), errors="coerce")
                price = pd.to_numeric(row.get("TradePrice", 0), errors="coerce")
                if pd.isna(qty) or pd.isna(price):
                    return 0.0
                asset_class = str(row.get("AssetClass", "")).upper().strip()
                mult = 100 if asset_class == "OPT" else 1
                gross = abs(float(qty)) * float(price) * mult
                side = str(row.get("Buy/Sell", "")).upper().strip()
                return gross if side == "SELL" else -gross
            platform = str(row.get("Platform", "")).strip()
            if platform == "Tiger":
                return -float(net_cash_val)
            return float(net_cash_val)

        net_cash = float(gt.apply(_cash_flow, axis=1).sum())

        if "RealizedPnLSgd" in gt.columns:
            rs = pd.to_numeric(gt["RealizedPnLSgd"], errors="coerce").fillna(0)
        else:
            rs = pd.to_numeric(gt.get("RealizedPnL", 0), errors="coerce").fillna(0)
        realized = float(rs.sum())

        gt_grp = gt.copy()
        gt_grp["_grp_key"] = gt_grp.apply(_option_identity_from_row, axis=1)
        gt_grp["_signed_qty"] = gt_grp.apply(_signed_qty_row, axis=1)

        for grp_key, sym_df in gt_grp.groupby("_grp_key"):
            symbol = _clean_str(sym_df.iloc[0].get("Symbol", ""))
            if not symbol:
                continue
            qty = float(sym_df["_signed_qty"].sum())
            if abs(qty) > 0.001:
                sym_df_sorted = sym_df.copy()
                sym_df_sorted["_sort_date"] = pd.to_datetime(
                    sym_df_sorted["TradeDate"], errors="coerce", dayfirst=True
                )
                latest = sym_df_sorted.sort_values("_sort_date", ascending=False).iloc[0]
                open_positions.append({
                    "symbol": symbol,
                    "description": _short_desc(latest.get("Description", ""), symbol),
                    "quantity": qty,
                    "asset_class": _clean_str(latest.get("AssetClass", "")),
                })

        for p in gt.get("Platform", pd.Series()).dropna().unique():
            pc = _clean_str(p)
            if pc:
                platforms.add(pc)

        for s in gt.get("Strategy", pd.Series()).dropna().unique():
            sc = _clean_str(s)
            if sc:
                strategy_tags.add(sc)

    campaign_notes_mask = journal_df["Tags"].apply(
        lambda t: group_name in _parse_tags(t) if pd.notna(t) else False
    )
    campaign_notes_df = journal_df[campaign_notes_mask]

    matching_notes = []
    latest_be = ""
    for _, r in campaign_notes_df.iterrows():
        matching_notes.append({
            "date": _normalize_date(r.get("Date", "")),
            "tags": _parse_tags(r.get("Tags", "")),
            "notes": _clean_str(r.get("Notes", "")),
            "breakeven": _clean_str(r.get("Breakeven", "")),
            "journal_id": _clean_str(r.get("JournalId", "")),
        })

    matching_notes.sort(key=lambda x: _to_dt(x["date"]), reverse=True)
    for note in matching_notes:
        if note["breakeven"]:
            latest_be = note["breakeven"]
            break

    timeline = []
    if not gt.empty:
        for _, r in gt.iterrows():
            raw_qty = pd.to_numeric(r.get("Quantity", 0), errors="coerce")
            if pd.notna(raw_qty):
                signed = abs(float(raw_qty))
                if str(r.get("Buy/Sell", "")).upper().strip() == "SELL":
                    signed = -signed
                qty_display = str(signed)
            else:
                qty_display = _clean_str(r.get("Quantity", ""))
            timeline.append({
                "date": _normalize_date(r.get("TradeDate", "")),
                "kind": "trade",
                "side": _clean_str(r.get("Buy/Sell", "")),
                "symbol": _clean_str(r.get("Symbol", "")),
                "description": _format_trade_label(r),
                "quantity": qty_display,
                "price": _clean_str(r.get("TradePrice", "")),
                "net_cash": _clean_str(r.get("NetCash", "")),
            })

    for note in matching_notes:
        timeline.append({
            "date": note["date"],
            "kind": "note",
            "text": note["notes"],
            "breakeven": note["breakeven"],
            "tags": note["tags"],
        })

    timeline.sort(key=lambda x: _to_dt(x["date"]) if x["date"] else datetime.min)

    journal_dates = [n["date"] for n in matching_notes if n["date"]]
    trade_dates = [_normalize_date(r.get("TradeDate", "")) for _, r in gt.iterrows()] if not gt.empty else []
    trade_dates = [d for d in trade_dates if d]

    def _min_date(dates):
        parsed = [(d, _to_dt(d)) for d in dates if d]
        parsed = [(s, dt) for s, dt in parsed if dt != datetime.min]
        return min(parsed, key=lambda x: x[1])[0] if parsed else ""

    def _max_date(dates):
        parsed = [(d, _to_dt(d)) for d in dates if d]
        parsed = [(s, dt) for s, dt in parsed if dt != datetime.min]
        return max(parsed, key=lambda x: x[1])[0] if parsed else ""

    if journal_dates:
        start_date = _min_date(journal_dates)
    elif trade_dates:
        start_date = _min_date(trade_dates)
    else:
        start_date = ""

    end_date = _max_date(journal_dates + trade_dates)

    days_running = 0
    if start_date:
        try:
            sd = datetime.strptime(start_date, "%d/%m/%Y")
            days_running = (datetime.now() - sd).days
        except Exception:
            pass

    # DTE = days to nearest option expiry among OPEN legs
    dte_nearest = None
    if open_positions:
        _exp_days = []
        for p in open_positions:
            if _clean_str(p.get("asset_class", "")).upper() != "OPT":
                continue
            desc = _clean_str(p.get("description", "")).upper()
            exp_dt = None

            # Format 1: "17JUN27" / "18SEP26" / "1JAN26"
            m = re.search(r"\b(\d{1,2})([A-Z]{3})(\d{2,4})\b", desc)
            if m:
                dd, mon, yy = m.group(1), m.group(2), m.group(3)
                yy = ("20" + yy) if len(yy) == 2 else yy
                try:
                    exp_dt = datetime.strptime(f"{dd}{mon}{yy}", "%d%b%Y")
                except Exception:
                    exp_dt = None

            # Format 2: "20260821" YYYYMMDD
            if exp_dt is None:
                m2 = re.search(r"\b(20\d{6})\b", desc)
                if m2:
                    try:
                        exp_dt = datetime.strptime(m2.group(1), "%Y%m%d")
                    except Exception:
                        exp_dt = None

            # Format 3: "260821" YYMMDD
            if exp_dt is None:
                m3 = re.search(r"\b(\d{6})\b", desc)
                if m3:
                    try:
                        exp_dt = datetime.strptime(m3.group(1), "%y%m%d")
                    except Exception:
                        exp_dt = None

            # Format 4: "JAN26" / "SEP2026" (month-only, assume 3rd Friday)
            if exp_dt is None:
                m4 = re.search(r"\b([A-Z]{3})\s?(\d{2,4})\b", desc)
                if m4:
                    mon, yy = m4.group(1), m4.group(2)
                    yy = ("20" + yy) if len(yy) == 2 else yy
                    try:
                        first = datetime.strptime(f"01{mon}{yy}", "%d%b%Y")
                        offset = (4 - first.weekday()) % 7
                        exp_dt = first.replace(day=1 + offset + 14)
                    except Exception:
                        exp_dt = None

            if exp_dt is not None:
                _exp_days.append(_dte_from_expiry(exp_dt))

        if _exp_days:
            dte_nearest = min(_exp_days)


    return {
        "group": group_name,
        "platforms": ", ".join(sorted(platforms)) if platforms else "—",
        "strategies": ", ".join(sorted(strategy_tags)) if strategy_tags else "",
        "net_cash": net_cash,
        "realized": realized,
        "open_positions": open_positions,
        "latest_be": latest_be,
        "n_trades": int(len(gt)) if not gt.empty else 0,
        "n_notes": len(matching_notes),
        "start_date": start_date,
        "end_date": end_date,
        "days_running": days_running,
        "dte_nearest": dte_nearest,
        "is_open": len(open_positions) > 0,
        "timeline": timeline,
    }


# ============================================================
# LINKAGE (Pass 2) — auto adjusted cost + strategy-aware Pos BE
# ============================================================

def _enrich_with_linkage(campaigns):
    by_name = {c["group"]: c for c in campaigns}
    all_names = set(by_name.keys())

    for c in campaigns:
        c["is_parent"] = _is_parent_strategy(c["group"])
        c["is_cycle"] = _is_cycle_strategy(c["group"])
        c["is_standalone"] = _is_standalone_strategy(c["group"])
        c["ticker"] = _extract_ticker(c["group"])
        c["parent_name"] = None
        c["adjusted_cost"] = c["net_cash"]
        c["premium_harvested"] = 0.0      # sum of CLOSED nested cycles' net cash
        c["open_cycles_net"] = 0.0        # sum of OPEN nested cycles' net cash
        c["position_be"] = None
        c["position_be_label"] = "Pos BE"
        c["closed_children"] = 0
        c["open_children"] = 0
        if c["is_cycle"]:
            c["parent_name"] = _get_parent_of(c["group"], all_names)

    for c in campaigns:
        if not c["is_parent"]:
            continue
        child_names = _get_children_of(c["group"], all_names)
        harvested = 0.0
        open_cycles_net = 0.0
        n_closed = 0
        n_open = 0
        for cn in child_names:
            child = by_name.get(cn)
            if child is None:
                continue
            if child["is_open"]:
                n_open += 1
                open_cycles_net += child["net_cash"]
            else:
                n_closed += 1
                harvested += child["net_cash"]
        c["premium_harvested"] = harvested
        c["open_cycles_net"] = open_cycles_net
        c["closed_children"] = n_closed
        c["open_children"] = n_open
        c["adjusted_cost"] = c["net_cash"] + harvested

        # ---- Pos BE only when it is unambiguous ----
        # Rule: single-anchor parent (LEAP/HOLD/STOCK/SHARES) with EXACTLY ONE open leg.
        # More than one open leg => multi-leg structure (synthetic after partial close,
        # collar, etc.) => we CANNOT express one BE point, so defer to manual BE.
        last_seg = _last_strategy_segment(c["group"])
        if last_seg in BE_ELIGIBLE_PARENT and len(c["open_positions"]) == 1:
            fp = c["open_positions"][0]
            asset_class = fp.get("asset_class", "").upper()
            n_units = abs(fp.get("quantity", 1))
            if n_units > 0:
                if asset_class == "OPT":
                    strike = _extract_strike_from_description(fp.get("description", ""))
                    if strike is not None:
                        # BE assuming you EXERCISE the long option (LEAP). Most people SELL,
                        # so this is labelled honestly as "if exercised".
                        c["position_be"] = strike + abs(c["adjusted_cost"]) / (100 * n_units)
                        c["position_be_label"] = "BE (if exercised)"
                elif asset_class == "STK":
                    c["position_be"] = abs(c["adjusted_cost"]) / n_units
                    c["position_be_label"] = "Pos BE"


# ============================================================
# CARD RENDERING (black card, actions inside, mobile-friendly)
# ============================================================

def _money(v):
    return f"{'+' if v >= 0 else ''}&#36;{v:,.2f}"


def _metric(label, value_html):
    return (
        f"<div class='metric-cell'>"
        f"<div class='metric-label'>{label}</div>"
        f"<div class='metric-value'>{value_html}</div>"
        f"</div>"
    )


def _open_positions_html(open_positions):
    if not open_positions:
        return "<div style='color:gray; font-size:12px; padding:2px 0;'>All positions closed</div>"
    out = ""
    for p in open_positions:
        qty = p["quantity"]
        qty_str = f"{qty:+.0f}" if abs(qty) >= 1 else f"{qty:+.4f}"
        qty_color = "#66FF99" if qty > 0 else "#FF9F1C"
        out += (
            f"<div style='padding:3px 0; display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;'>"
            f"<span style='color:{qty_color}; font-weight:bold; font-size:14px; min-width:38px;'>{qty_str}</span>"
            f"<span style='color:#DDD; font-size:13px;'>{_safe_html(p['description'])}</span>"
            f"</div>"
        )
    return out


def _metrics_html(c):
    tiles = []
    if c["is_parent"]:
        if abs(c["premium_harvested"]) > 0.01:
            adj_color = "#66FF99" if c["adjusted_cost"] >= 0 else "#FF6666"
            raw_color = "#66FF99" if c["net_cash"] >= 0 else "#FF6666"
            tiles.append(_metric("Adj Cost", f"<span style='color:{adj_color};'>{_money(c['adjusted_cost'])}</span>"))
            tiles.append(_metric("Harvested", f"<span style='color:#66FF99;'>{_money(c['premium_harvested'])}</span>"))
            tiles.append(_metric("Raw Cost", f"<span style='color:{raw_color}; font-size:13px;'>{_money(c['net_cash'])}</span>"))
        else:
            raw_color = "#66FF99" if c["net_cash"] >= 0 else "#FF6666"
            tiles.append(_metric("Cost", f"<span style='color:{raw_color};'>{_money(c['net_cash'])}</span>"))
        if c["position_be"] is not None:
            tiles.append(_metric(c.get("position_be_label", "Pos BE"),
                                 f"<span style='color:#FFC300;'>&#36;{c['position_be']:.2f}</span>"))
        elif c["latest_be"]:
            tiles.append(_metric("BE (manual)", f"<span style='color:#FFC300;'>{_safe_html(c['latest_be'])}</span>"))
    else:
        net_color = "#66FF99" if c["net_cash"] >= 0 else "#FF6666"
        tiles.append(_metric("Net Prem", f"<span style='color:{net_color};'>{_money(c['net_cash'])}</span>"))
        if c["latest_be"]:
            tiles.append(_metric("BE (manual)", f"<span style='color:#FFC300;'>{_safe_html(c['latest_be'])}</span>"))
        if abs(c["realized"]) > 0.01:
            rc = "#66FF99" if c["realized"] >= 0 else "#FF6666"
            rlabel = "Final PnL" if not c["is_open"] else "Realized"
            tiles.append(_metric(rlabel, f"<span style='color:{rc};'>{_money(c['realized'])}</span>"))

    # DTE — shown only when there's an open option with a parseable expiry
    dte = c.get("dte_nearest")
    if c["is_open"] and dte is not None:
        dte_color = "#FF6666" if dte <= 7 else ("#FFC300" if dte <= 21 else "white")
        if dte > 0:
            dte_str = f"{dte}d"
        elif dte == 0:
            dte_str = "TODAY"
        else:
            dte_str = f"EXPIRED {-dte}d ago"
        tiles.append(_metric("DTE", f"<span style='color:{dte_color};'>{dte_str}</span>"))

    return "<div class='metric-row'>" + "".join(tiles) + "</div>"


def _card_header_html(c, accent):
    if c["is_parent"]:
        type_label = "PARENT"
    elif c["is_cycle"]:
        type_label = "CYCLE"
    else:
        type_label = "STANDALONE"

    status_dot = "🟢" if c["is_open"] else "⚪"
    type_badge = (
        f"<span style='background:rgba(255,255,255,0.06); color:{accent}; "
        f"padding:1px 7px; border-radius:9px; font-size:9px; font-weight:bold; "
        f"letter-spacing:0.5px; margin-left:6px;'>{type_label}</span>"
    )

    subtitle_bits = []
    if c["strategies"]:
        subtitle_bits.append(_safe_html(c["strategies"]))
    if c["platforms"] and c["platforms"] != "—":
        subtitle_bits.append(_safe_html(c["platforms"]))
    if c["is_parent"] and (c["open_children"] or c["closed_children"]):
        subtitle_bits.append(
            f"<span style='color:#FF9F1C;'>{c['open_children']} open</span>/"
            f"<span style='color:#999;'>{c['closed_children']} closed</span> cyc"
        )
    subtitle = " · ".join(subtitle_bits)

    return (
        f"<div style='display:flex; align-items:center; gap:6px; flex-wrap:wrap;'>"
        f"<span style='font-size:11px;'>{status_dot}</span>"
        f"<span class='campaign-title' style='color:{accent}; font-size:17px; font-weight:bold;'>{_safe_html(c['group'])}</span>"
        f"{type_badge}"
        f"<span style='color:gray; font-size:11px; margin-left:4px;'>{c['n_trades']}T / {c['n_notes']}N</span>"
        f"</div>"
        + (f"<div style='color:#999; font-size:12px; margin-top:2px;'>{subtitle}</div>" if subtitle else "")
    )


def _timeline_html(c):
    events_by_date = {}
    for evt in c["timeline"]:
        d = evt["date"] or "—"
        events_by_date.setdefault(d, []).append(evt)

    html_out = ""
    for d in sorted(events_by_date.keys(), key=_to_dt, reverse=True):
        events = events_by_date[d]
        events.sort(key=lambda e: 0 if e["kind"] == "note" else 1)
        html_out += (
            f"<div style='margin-top:14px; margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid #444;'>"
            f"<span style='color:#FFC300; font-size:14px; font-weight:bold;'>{d}</span></div>"
        )
        for evt in events:
            if evt["kind"] == "trade":
                side_color = "#66FF99" if evt["side"] == "SELL" else "#FF9F1C"
                side_bg = "rgba(102,255,153,0.10)" if evt["side"] == "SELL" else "rgba(255,159,28,0.10)"
                try:
                    qty_f = float(evt['quantity'])
                    qty_str = f"{qty_f:+.0f}" if abs(qty_f) >= 1 else f"{qty_f:+.4f}"
                except Exception:
                    qty_str = evt['quantity']
                try:
                    price_str = f"{float(evt['price']):,.2f}"
                except Exception:
                    price_str = evt['price']
                try:
                    net_f = float(evt['net_cash'])
                    net_c = "#66FF99" if net_f >= 0 else "#FF6666"
                    net_str = f"{'+' if net_f >= 0 else ''}&#36;{net_f:,.2f}"
                except Exception:
                    net_c = "#CCC"
                    net_str = _safe_html(evt['net_cash'])
                html_out += (
                    f"<div style='padding:9px 12px; margin:5px 0; background:{side_bg}; "
                    f"border-left:3px solid {side_color}; border-radius:4px;'>"
                    f"<div style='display:flex; gap:10px; flex-wrap:wrap; margin-bottom:4px;'>"
                    f"<span style='color:{side_color}; font-weight:bold; font-size:13px;'>{_safe_html(evt['side'])}</span>"
                    f"<span style='color:white; font-size:13px; font-weight:600;'>{_safe_html(_short_desc(evt['description']))}</span>"
                    f"</div>"
                    f"<div style='display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px;'>"
                    f"<span style='color:#BBB; font-size:12px;'>{qty_str} @ {price_str}</span>"
                    f"<span style='color:{net_c}; font-size:14px; font-weight:bold;'>{net_str}</span>"
                    f"</div></div>"
                )
            else:
                be_extra = ""
                if evt["breakeven"]:
                    be_extra = (
                        f"<span style='color:#FFC300; font-size:11px; margin-left:8px; "
                        f"background:rgba(255,195,0,0.15); padding:2px 7px; border-radius:4px; font-weight:bold;'>"
                        f"BE {_safe_html(evt['breakeven'])}</span>"
                    )
                safe_text = _safe_html(evt['text'])
                safe_text = safe_text.replace("\n", "<br>") if safe_text else "<i>(empty)</i>"
                html_out += (
                    f"<div style='padding:9px 12px; margin:5px 0; background:rgba(255,153,204,0.08); "
                    f"border-left:3px solid #FF99CC; border-radius:4px;'>"
                    f"<div style='margin-bottom:5px;'>"
                    f"<span style='color:#FF99CC; font-size:11px; font-weight:bold;'>📝 NOTE</span>{be_extra}</div>"
                    f"<div style='color:#EEE; font-size:13px; line-height:1.5;'>{safe_text}</div></div>"
                )
    if not html_out:
        html_out = "<div style='color:gray; font-size:12px;'>No activity yet</div>"
    return html_out


def _render_campaign(c, nested=False):
    if c["is_parent"]:
        accent = "#00D4FF"
    elif c["is_cycle"]:
        accent = "#FF9F1C"
    else:
        accent = "#FFC300"
    border_color = accent if c["is_open"] else "#555"

    if nested:
        _, host = st.columns([0.4, 11.6])
    else:
        host = st.container()

    # Sanitize group into a valid key fragment
    card_key = "campcard_" + re.sub(r"[^0-9a-zA-Z]+", "_", c["group"])

    # Decide action buttons for this card
    actions = []  # list of (emoji, session_state_key, tooltip)
    if c["is_open"]:
        if c["is_cycle"]:
            actions.append(("🔓", "_pending_close_cycle", "Close Cycle — untag from shared trades, roll premium to parent"))
        elif c["is_parent"]:
            actions.append(("🏁", "_pending_mark_expired", "Mark Expired — adds $0 closing leg, keeps premium"))
            actions.append(("🔒", "_pending_force_close", "Force Close — NUCLEAR: sends to _ignore, hides all stats"))
        else:  # standalone
            actions.append(("🏁", "_pending_mark_expired", "Mark Expired — adds $0 closing leg, keeps premium"))

    tl_key = f"tl_{c['group']}"
    is_tl_open = st.session_state.get("_open_timeline") == c["group"]

    with host:
        with st.container(border=True, key=card_key):
            # Colored top accent bar (type / open-status color)
            st.markdown(
                f"<div style='height:3px; background:{border_color}; border-radius:3px; margin:0 0 6px 0;'></div>",
                unsafe_allow_html=True,
            )

            # Header row: title (left) + compact icon buttons (right)
            # 1 timeline btn always + 0-2 action btns => total 1-3 icon buttons
            n_btns = 1 + len(actions)
            col_ratios = [10] + [1] * n_btns
            head_cols = st.columns(col_ratios, vertical_alignment="center")
            with head_cols[0]:
                st.markdown(_card_header_html(c, accent), unsafe_allow_html=True)
            # Action buttons
            for i, (emoji, pending_key, tooltip) in enumerate(actions):
                with head_cols[i + 1]:
                    if st.button(emoji, key=f"{pending_key}_{c['group']}", use_container_width=True, help=tooltip):
                        st.session_state[pending_key] = c["group"]
                        st.rerun()
            # Timeline toggle (always last)
            with head_cols[-1]:
                tl_icon = "📖" if is_tl_open else "📜"
                tl_help = f"{c['n_trades']} trades / {c['n_notes']} notes — click to {'hide' if is_tl_open else 'view'} timeline"
                if st.button(tl_icon, key=tl_key, use_container_width=True, help=tl_help):
                    st.session_state["_open_timeline"] = None if is_tl_open else c["group"]
                    st.rerun()

            # Metrics + open positions
            st.markdown(
                _metrics_html(c)
                + "<div style='border-top:1px solid #2a2a2a; padding-top:6px; margin-top:4px;'>"
                + "<div style='color:gray; font-size:10px; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:2px;'>Open positions</div>"
                + _open_positions_html(c["open_positions"])
                + "</div>",
                unsafe_allow_html=True,
            )

            # Inline timeline lives INSIDE the card so it stays visually attached
            if is_tl_open:
                st.markdown(
                    f"<div style='background-color:#080b10; padding:10px 14px; border-radius:6px; "
                    f"margin-top:8px; border:1px solid #1e2530;'>{_timeline_html(c)}</div>",
                    unsafe_allow_html=True,
                )

        # Next-cycle hint under active parents with no open cycle (outside the card, small text)
        if c["is_parent"] and c["open_children"] == 0 and c["is_open"]:
            all_names = {cc["group"] for cc in st.session_state.get("_all_campaign_names", [])} or {c["group"]}
            suggested = _suggest_next_cycle_name(all_names, c["group"], strategy="PMCC")
            st.markdown(
                f"<div style='margin:4px 0 4px 4px; color:#999; font-size:12px;'>"
                f"💡 No active cycle. Tag trades: "
                f"<code style='background:rgba(255,195,0,0.15); color:#FFC300; padding:1px 6px; border-radius:3px;'>{suggested}</code>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-bottom:6px;'></div>", unsafe_allow_html=True)


# ============================================================
# TRADE FILTER
# ============================================================

def _filter_trades(df, symbol="", platform="All", group="All", days=90):
    if df.empty:
        return df
    result = df.copy()
    if symbol.strip():
        s = symbol.strip().upper()
        result = result[
            result["Symbol"].fillna("").str.upper().str.contains(s, na=False)
            | result.get("Description", pd.Series()).fillna("").str.upper().str.contains(s, na=False)
        ]
    if platform != "All":
        result = result[result["Platform"] == platform]
    if group != "All" and "Group" in result.columns:
        result = result[result["Group"].fillna("").apply(lambda g: group in _parse_groups(g))]
    if "TradeDate" in result.columns:
        result = result.copy()
        result["_sort_date"] = pd.to_datetime(result["TradeDate"], errors="coerce", dayfirst=True)
        if days > 0:
            cutoff = datetime.now() - timedelta(days=days)
            result = result[(result["_sort_date"] >= cutoff) | (result["_sort_date"].isna())]
        result = result.sort_values("_sort_date", ascending=False)
        result = result.drop(columns=["_sort_date"], errors="ignore")
    return result


# ============================================================
# NAMING HELP PANEL  (single source of truth + special instructions)
# ============================================================

def _render_naming_help():
    with st.expander("📖 Naming Convention & Workflow Cheat Sheet", expanded=False):
        st.markdown("""
<div style='font-size:13px; line-height:1.6;'>

## 🏷️ Naming

**Format:** `TICKER-STRATEGY-N` · Nested cycle: `TICKER-PARENT-N-CYCLE-M`

**🔵 Parent** (core hold): `DRAM-LEAP-1`, `SOFI-HOLD-1`, `SOFI-STOCK-1` — uses **LEAP, HOLD, STOCK, SHARES**

**🟠 Cycle** (nested under parent): `DRAM-LEAP-1-PMCC-1`, `SOFI-STOCK-1-CC-3` — uses **PMCC, CC, COLLAR, DIAG, CAL, WHEEL**

**🟡 Standalone**: `AAOX-SP-1`, `SOFI-SYN-1`, `SPX-IC-1` — uses **SP, CSP, SYN, IC, VERT, SPREAD, STRANGLE, STRADDLE**

**Naming rules**
- New LEAPS = new parent (`DRAM-LEAP-2`).
- New cycle = increment N (the card hints the next name).
- Rolls stay in the same cycle (tag both roll legs with the SAME cycle name).
- Cycles must be nested under their parent: `SOFI-LEAP-1-PMCC-1` ✅ not `SOFI-PMCC-1` ❌
- `_ignore` for accumulation trades you don't want tracked.

**🔁 Wheel** — parent `SOFI-STOCK-1` + CC cycles `SOFI-STOCK-1-CC-1`, `...-CC-2`. Each closed CC reduces the shares' Adjusted Cost automatically.

**🧮 PMCC** — parent `SOFI-LEAP-1` + short-call cycles `SOFI-LEAP-1-PMCC-1`, `...-PMCC-2`. Closed PMCCs roll into the LEAP's Adjusted Cost.

---

## 💰 What the engine calculates AUTOMATICALLY (don't type it)

- **Net Prem / Realized** — from tagged trades' NetCash.
- **Raw Cost** = parent's own net cash.
- **Harvested** = sum of **closed** nested cycles' net cash.
- **Adj Cost** = Raw Cost + Harvested. Updates every time a cycle closes.
- **Pos BE** — shown ONLY when unambiguous (see below).

You do **not** journal Adj Cost or premium collected. Nest cycles correctly and close them; the numbers appear.

---

## 📏 Pos BE — shown only when unambiguous

**Auto BE appears** for a single-anchor parent (**LEAP / HOLD / STOCK / SHARES**) with **exactly ONE open leg**:
- Stock parent → `Pos BE = |Adj Cost| / shares`
- Option parent → `BE (if exercised) = strike + |Adj Cost| / (100 × contracts)` — reference basis, not your exit price.

**Auto BE is hidden** (defers to your typed BE) for anything multi-leg: synthetics after partial close, collars, spreads, straddles, strangles, ICs, ZEBRAs. A single BE point can't represent those — some have two breakevens or a curve.

Rule of thumb: **auto BE where provably correct, manual BE everywhere else, never a fake number.**

---

## ✍️ When to type BE manually (in the Breakeven field of a note)

1. **Multi-leg structure** — spread, IC, straddle, synthetic-after-partial-close, ZEBRA, collar. The engine can't compute; you're the source of truth.
2. **LEAP you plan to SELL, not exercise** — auto shows "BE if exercised". If your real exit is a sell price, type that as BE with reasoning.
3. **Broker missed an assignment or stock leg** — Adj Cost is wrong until data is fixed. Override with a manual BE anchoring the truth.
4. **Trading-plan BE, not math BE** — a mental stop / risk limit is your real BE regardless of math. Journal it.

For a normal wheel / PMCC / single-leg SP, the auto number is trustworthy. Don't retype it.

---

## 📝 When to write a journal NOTE

Rule of thumb: **Is this a fact the engine can derive, or a thought only I have?**
- **Fact** (trade cash, single-leg BE, adj cost) → don't type it, engine handles it.
- **Thought** → journal it.

Journal-worthy content:
- **Thesis** — why opening this campaign
- **Exit rule** — "roll if delta > 40, close if underlying breaks 22"
- **Manual BE** for multi-leg structures
- **Reflection / postmortem** — what worked, what to change
- **External context** — earnings dates, macro events, dividends
- **Decision rationale** — especially defensive rolls or judgment calls

Skip journaling: routine mechanical rolls, "sold CC for +$X" (that's just a trade log).

---

## 🔗 Combine trades (for rolls) — still useful

A roll is one *decision* made of two *trades*. Combine merges tags, sums net premium, dates to the later leg. Use it when you have something to **say** about the roll.

**Combine + journal a roll when:**
- Recording **why** you rolled (thesis / reasoning)
- Logging a **new BE** for a multi-leg outcome
- Making a **judgment call** you might second-guess later
- **Defensive roll** (rolled for debit, extended risk)
- **Rule deviation** ("rolling later than my 21DTE rule because...")

**Skip Combine when:**
- Purely mechanical / by-rule roll
- Nothing to say beyond "took profit"
- Timeline is self-explanatory

---

## 🏷️ Tags — two very different kinds

**Group tags on trades** (in Assign Trades section) — **MANDATORY forever.**
Without a Group tag, a trade is invisible to the engine. Every imported trade needs either a real Group tag (e.g. `SOFI-LEAP-1-PMCC-2`) or `_ignore`. Never leave blanks long-term.

**Tags in the Journal Entry dialog** — links a note to a campaign so it appears on that card's timeline.
- Use when the note is campaign-specific (thesis, plan, manual BE, reflection).
- Skip when it's a general market thought (leave blank, it lives in "All Notes").
- Keep it minimal — usually just ONE campaign name. Ticker/platform/strategy are already on the card.

Old way (over-tagged): `SOFI,SOFI-LEAP-1,SOFI-LEAP-1-PMCC-2,Tiger,Roll,PMCC`
New way: `SOFI-LEAP-1-PMCC-2` — done.

---

## ⚙️ Card actions (compact icons, top-right of each card)

- **🔓 Close Cycle** (cycles): removes the cycle tag from trades that have *multiple* tags, so its premium rolls up to the parent's Adj Cost. Trades tagged *only* with the cycle are left alone.
- **🏁 Mark Expired** (standalone & parent): use when the broker didn't import the closing/expiry trade. Adds a $0 closing leg → position nets flat → moves to closed. Premium & realized PRESERVED. Normal way to close an expired-worthless SP/CSP.
- **🔒 Force Close** (parent — nuclear): tags trades `_ignore`, hiding them from ALL stats. Only for abandoning tracking, delistings, or bad data. Do NOT use for normal expiry.
- **📜 Timeline** (all): toggles inline timeline. Only one opens at a time.

</div>
""", unsafe_allow_html=True)


# ============================================================
# ADD ENTRY DIALOG
# ============================================================

@st.dialog("➕ Add Journal Entry")
def _add_entry_dialog():
    prefill = st.session_state.pop("_journal_prefill", None)
    default_date = date.today().strftime("%d/%m/%Y")
    default_tags = default_strategy = default_notes = ""

    if prefill:
        default_date = prefill.get("date") or default_date
        default_tags = prefill.get("tags") or ""
        default_strategy = prefill.get("strategy") or ""
        default_notes = prefill.get("notes") or ""
        st.session_state["dialog_date"] = default_date
        st.session_state["dialog_strategy"] = default_strategy
        st.session_state["dialog_notes"] = default_notes
        st.session_state["dialog_be"] = ""
        st.session_state["dialog_new_campaign"] = ""
        # Split prefill tags: known campaigns → multiselect, others → descriptive
        _prefill_parsed = _parse_tags(default_tags)
        _existing_now = _get_all_campaign_groups(journal_df, trades_df)
        st.session_state["dialog_campaign_ms"] = [t for t in _prefill_parsed if t in _existing_now]
        st.session_state["dialog_desc_tags"] = ",".join([t for t in _prefill_parsed if t not in _existing_now])
        st.session_state["_dialog_prefill_summary"] = prefill.get("summary", "")

    prefill_summary = st.session_state.get("_dialog_prefill_summary", "")
    if prefill_summary:
        st.success(f"✅ Pre-filled from trade: {prefill_summary}")

    entry_date_str = st.text_input("Date (DD/MM/YYYY)", value=default_date, key="dialog_date")

    with st.expander("📊 Reference trades (optional)", expanded=False):
        filter_col, action_col = st.columns([3, 2])
        with filter_col:
            symbol_filter = st.text_input("Filter by symbol", key="ref_symbol_filter",
                                          placeholder="e.g. SOFI", label_visibility="collapsed")
        matching = _filter_trades(trades_df, symbol=symbol_filter, days=365)

        selected_count = sum(1 for k, v in st.session_state.items()
                             if k.startswith("trade_check_") and v is True)

        with action_col:
            if selected_count >= 2:
                if st.button(f"📋 Combine ({selected_count})", type="primary",
                             use_container_width=True, key="combine_btn_top"):
                    selected_trades_data = []
                    for i, (_, r) in enumerate(matching.head(10).iterrows()):
                        if st.session_state.get(f"trade_check_{i}", False):
                            selected_trades_data.append(r)
                    if len(selected_trades_data) >= 2:
                        all_tags = []
                        for tr in selected_trades_data:
                            for t in _parse_tags(_trade_row_to_tags(tr)):
                                if t not in all_tags:
                                    all_tags.append(t)
                        strategies = [_trade_row_to_strategy(tr) for tr in selected_trades_data]
                        strategies = [s for s in strategies if s]
                        combined_strategy = max(set(strategies), key=strategies.count) if strategies else ""
                        dates = [_normalize_date(_clean_str(tr.get("TradeDate", ""))) for tr in selected_trades_data]
                        dates = [d for d in dates if d]

                        def _latest_ddmmyyyy(dts):
                            parsed = []
                            for d in dts:
                                try:
                                    parsed.append((datetime.strptime(d, "%d/%m/%Y"), d))
                                except Exception:
                                    pass
                            return max(parsed, key=lambda x: x[0])[1] if parsed else date.today().strftime("%d/%m/%Y")

                        combined_date = _latest_ddmmyyyy(dates)
                        notes_lines = []
                        net_total = 0.0
                        for tr in selected_trades_data:
                            notes_lines.append(_trade_row_to_notes_prefill(tr))
                            try:
                                net_total += float(tr.get("NetCash", 0))
                            except Exception:
                                pass
                        notes_lines.append(f"Net: {'+' if net_total >= 0 else ''}${net_total:,.2f}")
                        combined_notes = "\n".join(notes_lines)
                        summary_parts = []
                        for tr in selected_trades_data[:2]:
                            summary_parts.append(f"{_clean_str(tr.get('Buy/Sell', ''))} {_format_trade_label(tr)}")
                        combined_summary = " + ".join(summary_parts)
                        if len(selected_trades_data) > 2:
                            combined_summary += f" (+{len(selected_trades_data) - 2} more)"
                        st.session_state["_journal_prefill"] = {
                            "date": combined_date, "tags": ",".join(all_tags),
                            "strategy": combined_strategy, "notes": combined_notes,
                            "summary": combined_summary,
                        }
                        for k in list(st.session_state.keys()):
                            if k.startswith("trade_check_"):
                                st.session_state.pop(k, None)
                        st.session_state["_reopen_add_dialog"] = True
                        st.rerun()
            else:
                st.caption(f"{selected_count} selected" if selected_count else "Select 2+ to combine")

        if matching.empty:
            st.caption(f"No matches for '{symbol_filter}'." if symbol_filter.strip() else "Type a symbol above.")
        else:
            st.caption(f"Showing {min(len(matching), 10)} of {len(matching)} matches")
            for i, (_, r) in enumerate(matching.head(10).iterrows()):
                trade_date_raw = _clean_str(r.get("TradeDate", ""))
                trade_date = trade_date_raw.split(" ")[0] if " " in trade_date_raw else trade_date_raw
                side = _clean_str(r.get("Buy/Sell", ""))
                platform = _clean_str(r.get("Platform", ""))
                label = _short_desc(r.get("Description", ""), r.get("Symbol", ""))
                qty = _clean_str(r.get("Quantity", ""))
                price = _clean_str(r.get("TradePrice", ""))
                try:
                    net_f = float(r.get("NetCash", 0))
                    net_display = f"{'+' if net_f >= 0 else ''}${net_f:,.0f}"
                    net_color = "#0A7B3E" if net_f >= 0 else "#DC2626"
                except Exception:
                    net_display = ""
                    net_color = "#666"
                side_color = "#0A7B3E" if side == "SELL" else "#D97706"
                with st.container(border=True):
                    ck_col, info_col, act_col = st.columns([0.7, 5, 1])
                    with ck_col:
                        st.checkbox("sel", key=f"trade_check_{i}", label_visibility="collapsed")
                    with info_col:
                        current_groups = _parse_groups(r.get("Group", ""))
                        group_html = ""
                        if current_groups:
                            group_badges = " ".join([
                                f"<span style='background:rgba(255,195,0,0.15); color:#B45309; "
                                f"padding:1px 6px; border-radius:3px; font-size:10px; font-weight:600;'>{_safe_html(g)}</span>"
                                for g in current_groups
                            ])
                            group_html = f"<div style='margin-top:3px;'>{group_badges}</div>"
                        st.markdown(
                            f"<div style='font-size:11px; color:#888;'>{trade_date} · {platform}</div>"
                            f"<div style='margin-top:2px;'>"
                            f"<span style='color:{side_color}; font-weight:700; font-size:13px;'>{_safe_html(side)}</span> "
                            f"<span style='font-size:13px;'>{_safe_html(label)}</span></div>"
                            f"<div style='font-size:11px; color:#666; margin-top:2px;'>"
                            f"qty {_safe_html(qty)} @ {_safe_html(price)} · "
                            f"<span style='color:{net_color}; font-weight:600;'>{net_display}</span></div>"
                            f"{group_html}",
                            unsafe_allow_html=True,
                        )
                    with act_col:
                        if st.button("📋", key=f"copy_trade_{i}", help="Copy this trade only"):
                            st.session_state["_journal_prefill"] = {
                                "date": _normalize_date(trade_date_raw),
                                "tags": _trade_row_to_tags(r),
                                "strategy": _trade_row_to_strategy(r),
                                "notes": _trade_row_to_notes_prefill(r),
                                "summary": f"{trade_date} {side} {label}",
                            }
                            for k in list(st.session_state.keys()):
                                if k.startswith("trade_check_"):
                                    st.session_state.pop(k, None)
                            st.session_state["_reopen_add_dialog"] = True
                            st.rerun()

    # ============================================================
    # TAGS — multiselect + context suggestions + create-new + descriptive
    # ============================================================
    st.markdown("**Link to campaign(s)**")

    # All existing campaign tags (recompute inside dialog — safe & cheap)
    _existing_campaigns = _get_all_campaign_groups(journal_df, trades_df)

    # Context: derive ticker from reference-trade filter, or from prefill's first tag
    _ctx_ticker = ""
    _ref_sym = _clean_str(st.session_state.get("ref_symbol_filter", "")).upper()
    if _ref_sym:
        _ctx_ticker = _ref_sym
    else:
        _first_pf = _parse_tags(default_tags)
        if _first_pf and "-" in _first_pf[0]:
            _ctx_ticker = _first_pf[0].split("-")[0].upper()
        elif _first_pf:
            _ctx_ticker = _first_pf[0].upper()

    _suggested = [g for g in _existing_campaigns
                  if _ctx_ticker and g.upper().startswith(_ctx_ticker + "-")]

    # Show context-aware suggestions above the multiselect
    if _suggested:
        st.markdown(
            f"<div class='naming-hint'>💡 <b>Suggested for {_safe_html(_ctx_ticker)}:</b> "
            + " ".join([f"<code>{_safe_html(g)}</code>" for g in _suggested[:10]])
            + "</div>",
            unsafe_allow_html=True,
        )

    st.multiselect(
        "Existing campaigns (search & click)",
        options=_existing_campaigns,
        key="dialog_campaign_ms",
        placeholder="Search or pick campaign(s) this note belongs to",
        help="Type to search. Add as many as needed — usually just one.",
    )

    st.text_input(
        "➕ Create new campaign tag (only when starting a new one)",
        placeholder="e.g. SOFI-LEAP-2",
        help="Format: TICKER-STRATEGY-N (parent) or TICKER-PARENT-N-CYCLE-M (nested cycle)",
        key="dialog_new_campaign",
    )

    st.text_input(
        "Descriptive tags (optional, comma-separated)",
        placeholder="e.g. Roll, Defensive, Earnings, Reflection",
        help="Free-text context tags. NOT linked to campaigns.",
        key="dialog_desc_tags",
    )

    strategy_input = st.text_input("Strategy", value=default_strategy,
                                   placeholder="e.g. SP, CC, PMCC, Roll", help="Added as a tag on save",
                                   key="dialog_strategy")
    notes = st.text_area("Notes", value=default_notes,
                         placeholder="e.g. +$450 credit, total $1450 premium", height=120, key="dialog_notes")
    breakeven = st.text_input("Breakeven (optional)", placeholder="e.g. 25.50", key="dialog_be")

    st.markdown("---")
    c1, c2 = st.columns(2)

    if c1.button("✅ Save", type="primary", use_container_width=True):
        if not notes.strip():
            st.error("Notes field cannot be empty.")
            return
        parsed_date = _normalize_date(entry_date_str)
        if not parsed_date:
            st.error("Invalid date. Please use DD/MM/YYYY format.")
            return
        try:
            existing = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
        except Exception:
            existing = pd.DataFrame(columns=JOURNAL_SCHEMA)
        for col in JOURNAL_SCHEMA:
            if col not in existing.columns:
                existing[col] = ""

        # Combine: multiselect campaigns + new-campaign field + descriptive + strategy
        parsed_tags = list(st.session_state.get("dialog_campaign_ms", []))
        _new_camp = _clean_str(st.session_state.get("dialog_new_campaign", ""))
        if _new_camp and _new_camp not in parsed_tags:
            parsed_tags.append(_new_camp)
        for t in _parse_tags(st.session_state.get("dialog_desc_tags", "")):
            if t not in parsed_tags:
                parsed_tags.append(t)
        strategy_clean = strategy_input.strip()
        if strategy_clean and strategy_clean not in parsed_tags:
            parsed_tags.append(strategy_clean)

        new_row = {
            "JournalId": str(uuid.uuid4()),
            "CreatedAt": date.today().strftime("%d/%m/%Y"),
            "Date": parsed_date,
            "Tags": _join_tags(parsed_tags),
            "Notes": notes.strip(),
            "Breakeven": breakeven.strip(),
        }
        new_df = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
        _save_journal(new_df)
        for k in ["dialog_date", "dialog_campaign_ms", "dialog_new_campaign",
                  "dialog_desc_tags", "dialog_strategy", "dialog_notes", "dialog_be"]:
            st.session_state.pop(k, None)
        st.session_state.pop("_dialog_prefill_summary", None)
        for k in list(st.session_state.keys()):
            if k.startswith("trade_check_"):
                st.session_state.pop(k, None)
        st.success("✅ Entry saved.")
        st.cache_data.clear()
        st.rerun()

    if c2.button("❌ Cancel", use_container_width=True):
        for k in ["dialog_date", "dialog_campaign_ms", "dialog_new_campaign",
                  "dialog_desc_tags", "dialog_strategy", "dialog_notes", "dialog_be"]:
            st.session_state.pop(k, None)
        st.session_state.pop("_dialog_prefill_summary", None)
        for k in list(st.session_state.keys()):
            if k.startswith("trade_check_"):
                st.session_state.pop(k, None)
        st.rerun()



if st.session_state.pop("_reopen_add_dialog", False):
    _add_entry_dialog()


# ============================================================
# TOP CONTROLS
# ============================================================

top_c1, _ = st.columns([2, 6])
if top_c1.button("➕ Add Journal Entry", use_container_width=True, type="primary"):
    _add_entry_dialog()

_render_naming_help()


# ============================================================
# COMPUTE CAMPAIGNS
# ============================================================

all_groups = _get_all_campaign_groups(journal_df, trades_df)
campaigns = []
if all_groups:
    campaigns = [_compute_campaign(g, trades_df, journal_df) for g in all_groups]
    _enrich_with_linkage(campaigns)
    st.session_state["_all_campaign_names"] = campaigns


# ============================================================
# SECTION 1 — CAMPAIGNS (ticker dropdown filter + collapsible ticker groups)
# ============================================================

st.markdown("<div class='section-title'>📊 Campaigns</div>", unsafe_allow_html=True)

if not campaigns:
    st.info("No campaigns yet. Tag trades in the Assign section below to get started.")
else:
    by_name = {c["group"]: c for c in campaigns}

    tickers = defaultdict(list)
    for c in campaigns:
        tickers[c["ticker"]].append(c)

    ctrl_c1, ctrl_c2 = st.columns([3, 2])
    with ctrl_c1:
        ticker_options = ["All"] + sorted(tickers.keys())
        selected_ticker = st.selectbox("Ticker filter", ticker_options, index=0, key="ticker_filter")
    with ctrl_c2:
        show_closed = st.toggle("Show closed", value=False, key="show_closed_toggle")

    if selected_ticker != "All":
        view_tickers = {selected_ticker: tickers[selected_ticker]}
    else:
        view_tickers = dict(tickers)

    def _visible(cs):
        return [c for c in cs if c["is_open"] or show_closed]

    rendered_any = False
    for ticker in sorted(view_tickers.keys()):
        cs = view_tickers[ticker]
        visible_cs = _visible(cs)
        if not visible_cs:
            continue

        rendered_any = True
        open_count = sum(1 for c in cs if c["is_open"])
        total_count = len(cs)
        exp_label = f"{ticker}   ·   {open_count} open / {total_count} total"
        with st.expander(exp_label, expanded=(selected_ticker != "All" or open_count > 0)):
            parents = [c for c in cs if c["is_parent"]]
            cycles = [c for c in cs if c["is_cycle"]]
            standalones = [c for c in cs if c["is_standalone"]]
            others = [c for c in cs if not (c["is_parent"] or c["is_cycle"] or c["is_standalone"])]

            parents.sort(key=lambda x: (not x["is_open"], -x["days_running"]))

            used_cycles = set()
            for parent in parents:
                if not parent["is_open"] and not show_closed:
                    continue
                _render_campaign(parent)
                child_names = set(_get_children_of(parent["group"], set(by_name.keys())))
                child_cards = [c for c in cycles if c["group"] in child_names]
                child_cards.sort(key=lambda x: (not x["is_open"], -x["days_running"]))
                for child in child_cards:
                    used_cycles.add(child["group"])
                    if not child["is_open"] and not show_closed:
                        continue
                    _render_campaign(child, nested=True)

            orphan_cycles = [c for c in cycles if c["group"] not in used_cycles]
            for c in orphan_cycles:
                if not c["is_open"] and not show_closed:
                    continue
                _render_campaign(c)
                if c["parent_name"] is None:
                    st.markdown(
                        f"<div style='color:#FF9F1C; font-size:12px; margin-bottom:8px;'>"
                        f"⚠️ No parent found. To link, rename to nested format e.g. "
                        f"<code>{c['ticker']}-LEAP-1-{_last_strategy_segment(c['group'])}-1</code></div>",
                        unsafe_allow_html=True,
                    )

            for c in standalones:
                if not c["is_open"] and not show_closed:
                    continue
                _render_campaign(c)

            for c in others:
                if not c["is_open"] and not show_closed:
                    continue
                _render_campaign(c)

    if not rendered_any:
        st.caption("No active campaigns. Toggle 'Show closed' to see history. 🎉")

st.markdown("---")


# ============================================================
# SECTION 2 — ASSIGN TRADES TO CAMPAIGNS
# ============================================================

st.markdown("<div class='section-title'>🔗 Assign Trades to Campaigns</div>", unsafe_allow_html=True)
st.caption(
    "Edit the Group column to assign trades. Nest cycles under parents: "
    "`DRAM-LEAP-1,DRAM-LEAP-1-PMCC-3`. Use `_ignore` for accumulation trades."
)

if trades_df.empty:
    st.info("No trades in trades_history.csv")
else:
    unassigned_count = 0
    if "Group" in trades_df.columns:
        unassigned_count = int(
            trades_df["Group"].fillna("").apply(lambda g: _clean_str(g) == "").sum()
        )

    expander_label = "Edit trade Group assignments"
    if unassigned_count > 0:
        expander_label = f"⚠️ Edit trade Group assignments ({unassigned_count} unassigned)"

    with st.expander(expander_label, expanded=(unassigned_count > 0)):
        assign_c1, assign_c2, assign_c3 = st.columns(3)
        with assign_c1:
            assign_platforms = ["All"] + sorted(trades_df["Platform"].dropna().unique().tolist())
            assign_platform = st.selectbox("Broker", assign_platforms, key="assign_platform")
        with assign_c2:
            assign_symbol = st.text_input("Symbol filter", key="assign_symbol", placeholder="e.g. SOFI")
        with assign_c3:
            assign_days = st.selectbox(
                "Time range", [30, 90, 180, 365, 9999],
                format_func=lambda x: {30: "30 days", 90: "90 days", 180: "180 days", 365: "1 year", 9999: "All"}.get(x, str(x)),
                index=2, key="assign_days",
            )

        show_unassigned_only = st.checkbox("Show only blank Group (excludes _ignore)", key="assign_show_unassigned")

        # When hunting blanks, ignore the date window so nothing hides outside 180d
        effective_days = 0 if show_unassigned_only else assign_days
        assign_result = _filter_trades(trades_df, symbol=assign_symbol, platform=assign_platform, days=effective_days)

        if show_unassigned_only and "Group" in assign_result.columns:
            assign_result = assign_result[assign_result["Group"].fillna("").apply(lambda g: _clean_str(g) == "")]

        _blank_now = int(assign_result["Group"].fillna("").apply(lambda g: _clean_str(g) == "").sum()) if "Group" in assign_result.columns else 0
        st.caption(f"Showing {len(assign_result)} trade(s) · {_blank_now} blank in view")

        if assign_symbol.strip():
            ticker = assign_symbol.strip().upper()
            existing_for_ticker = sorted([g for g in all_groups if g.upper().startswith(ticker + "-")])
            if existing_for_ticker:
                st.markdown(
                    f"<div class='naming-hint'>💡 <b>Existing {ticker} tags:</b> "
                    + " ".join([f"<code>{g}</code>" for g in existing_for_ticker[:15]]) + "</div>",
                    unsafe_allow_html=True
                )

        if assign_result.empty:
            st.info("No trades match filters.")
        else:
            assign_result = assign_result.copy()
            assign_display_cols = [c for c in [
                "TradeDate", "Platform", "Symbol", "Description", "AssetClass",
                "Buy/Sell", "Quantity", "TradePrice", "NetCash", "Group",
            ] if c in assign_result.columns]
            if "Group" not in assign_result.columns:
                assign_result["Group"] = ""
                assign_display_cols.append("Group")
            editor_df_assign = assign_result[assign_display_cols].reset_index(drop=True)
            for col in ["Group"]:
                if col in editor_df_assign.columns:
                    editor_df_assign[col] = editor_df_assign[col].fillna("").astype(str)
                    editor_df_assign[col] = editor_df_assign[col].replace("nan", "").replace("None", "")

            assign_editor_key = "assign_trades_editor"
            edited_assign_df = st.data_editor(
                editor_df_assign.head(100), use_container_width=True, hide_index=True,
                disabled=[c for c in editor_df_assign.columns if c != "Group"],
                key=assign_editor_key,
                column_config={
                    "Group": st.column_config.TextColumn(
                        "Group", help="TICKER-STRATEGY-N. Comma-separate for multi. _ignore to skip.",
                        width="medium",
                    ),
                }
            )

            ignore_col, save_col = st.columns([1, 1])

            if ignore_col.button("🚫 Mark visible blank rows as _ignore", use_container_width=True,
                                 type="secondary", key="bulk_ignore"):
                try:
                    state = st.session_state.get(assign_editor_key, None)
                    working_df = edited_assign_df.copy()
                    if isinstance(state, dict) and "edited_rows" in state:
                        for row_pos, changes in state.get("edited_rows", {}).items():
                            try:
                                row_pos = int(row_pos)
                            except Exception:
                                continue
                            for col, value in changes.items():
                                if col in working_df.columns and row_pos < len(working_df):
                                    working_df.iloc[row_pos, working_df.columns.get_loc(col)] = value
                    full_trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
                    if "Group" not in full_trades.columns:
                        full_trades["Group"] = ""
                    key_cols = ["Platform", "TradeDate", "Symbol", "Buy/Sell", "Quantity", "TradePrice", "NetCash"]

                    def make_key(df):
                        parts = df[key_cols].copy()
                        for c in key_cols:
                            parts[c] = parts[c].fillna("").astype(str).str.strip()
                        return parts.agg("|".join, axis=1)

                    full_trades["_TradeKey"] = make_key(full_trades)
                    working_df["_TradeKey"] = make_key(working_df)
                    blank_mask = working_df["Group"].fillna("").apply(lambda g: _clean_str(g) == "")
                    blank_keys = working_df[blank_mask]["_TradeKey"].tolist()
                    if not blank_keys:
                        st.warning("No visible rows with blank Group to mark.")
                    else:
                        mask = full_trades["_TradeKey"].isin(blank_keys)
                        full_trades.loc[mask, "Group"] = "_ignore"
                        full_trades = full_trades.drop(columns=["_TradeKey"], errors="ignore")
                        full_trades.to_csv(TRADES_HISTORY_FILE, index=False)
                        st.success(f"✅ Marked {len(blank_keys)} trades as `_ignore`.")
                        st.cache_data.clear()
                        st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")

            if save_col.button("💾 Save Group Assignments", use_container_width=True,
                               type="primary", key="save_assignments"):
                try:
                    state = st.session_state.get(assign_editor_key, None)
                    if isinstance(state, dict) and "edited_rows" in state:
                        edited_assign_df = edited_assign_df.copy()
                        for row_pos, changes in state.get("edited_rows", {}).items():
                            try:
                                row_pos = int(row_pos)
                            except Exception:
                                continue
                            for col, value in changes.items():
                                if col in edited_assign_df.columns and row_pos < len(edited_assign_df):
                                    edited_assign_df.iloc[row_pos, edited_assign_df.columns.get_loc(col)] = value
                    full_trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
                    if "Group" not in full_trades.columns:
                        full_trades["Group"] = ""
                    key_cols = ["Platform", "TradeDate", "Symbol", "Buy/Sell", "Quantity", "TradePrice", "NetCash"]

                    def make_key(df):
                        parts = df[key_cols].copy()
                        for c in key_cols:
                            parts[c] = parts[c].fillna("").astype(str).str.strip()
                        return parts.agg("|".join, axis=1)

                    full_trades["_TradeKey"] = make_key(full_trades)
                    edited_assign_df["_TradeKey"] = make_key(edited_assign_df)
                    edited_assign_df["Group"] = edited_assign_df["Group"].apply(lambda g: ",".join(_parse_groups(g)))
                    updates = edited_assign_df[["_TradeKey", "Group"]].copy()
                    updates = updates.drop_duplicates(subset=["_TradeKey"], keep="last")
                    full_trades = full_trades.merge(updates, on="_TradeKey", how="left", suffixes=("", "_new"))
                    if "Group_new" in full_trades.columns:
                        full_trades["Group"] = full_trades["Group_new"].combine_first(full_trades["Group"])
                        full_trades.drop(columns=["Group_new"], inplace=True)
                    full_trades = full_trades.drop(columns=["_TradeKey"], errors="ignore")
                    full_trades.to_csv(TRADES_HISTORY_FILE, index=False)
                    st.success("✅ Group assignments saved.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")

st.markdown("---")


# ============================================================
# SECTION 3 — ALL NOTES
# ============================================================

st.markdown("<div class='section-title'>📝 All Notes</div>", unsafe_allow_html=True)

if journal_df.empty:
    st.info("No journal entries yet.")
else:
    filter_c1, filter_c2 = st.columns([2, 3])
    with filter_c1:
        selected_tags = st.multiselect("Filter by tags (AND)", options=ALL_TAGS,
                                       help="Show entries that contain ALL selected tags")
    with filter_c2:
        search_text = st.text_input("Search in notes", placeholder="Substring search on Notes field")

    filtered = journal_df.copy()
    if selected_tags:
        def has_all_tags(tag_str):
            entry_tags = set(_parse_tags(tag_str))
            return all(t in entry_tags for t in selected_tags)
        filtered = filtered[filtered["Tags"].apply(has_all_tags)]
    if search_text.strip():
        filtered = filtered[filtered["Notes"].fillna("").str.contains(search_text.strip(), case=False, na=False)]

    filtered = filtered.copy()
    filtered["_sort_date"] = pd.to_datetime(filtered["Date"], errors="coerce", dayfirst=True)
    filtered = filtered.sort_values("_sort_date", ascending=False)
    filtered = filtered.drop(columns=["_sort_date"])

    st.caption(f"Showing **{len(filtered)}** of {len(journal_df)} entries")

    if filtered.empty:
        st.info("No entries match filters.")
    else:
        display_cols = ["Select", "Date", "Tags", "Notes", "Breakeven", "CreatedAt", "JournalId"]
        editor_df = filtered.copy()
        editor_df["Select"] = False
        editor_df = editor_df[display_cols].reset_index(drop=True)
        editor_key = "trade_journal_editor"

        edited_df = st.data_editor(
            editor_df, use_container_width=True, hide_index=True,
            disabled=["CreatedAt", "JournalId"], key=editor_key,
            column_config={
                "Select": st.column_config.CheckboxColumn("🗑", help="Check to mark for delete", default=False, width="small"),
                "Date": st.column_config.TextColumn("Date"),
                "Tags": st.column_config.TextColumn("Tags"),
                "Notes": st.column_config.TextColumn("Notes"),
                "Breakeven": st.column_config.TextColumn("Breakeven"),
                "CreatedAt": st.column_config.TextColumn("Created", disabled=True),
                "JournalId": st.column_config.TextColumn("ID", disabled=True),
            }
        )

        col_save, col_delete = st.columns([1, 1])

        if col_save.button("💾 Save Changes", use_container_width=True, type="primary"):
            try:
                state = st.session_state.get(editor_key, None)
                if isinstance(state, dict) and "edited_rows" in state:
                    edited_df = edited_df.copy()
                    for row_pos, changes in state.get("edited_rows", {}).items():
                        try:
                            row_pos = int(row_pos)
                        except Exception:
                            continue
                        for col, value in changes.items():
                            if col in edited_df.columns and row_pos < len(edited_df):
                                edited_df.iloc[row_pos, edited_df.columns.get_loc(col)] = value
                for col in ["Date", "Tags", "Notes", "Breakeven"]:
                    if col in edited_df.columns:
                        edited_df[col] = edited_df[col].fillna("").astype(str)
                        edited_df[col] = edited_df[col].replace("nan", "").replace("None", "")
                edited_df["Tags"] = edited_df["Tags"].apply(lambda s: _join_tags(_parse_tags(s)))
                edited_df["Date"] = edited_df["Date"].apply(_normalize_date)
                full_df = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
                for col in JOURNAL_SCHEMA:
                    if col not in full_df.columns:
                        full_df[col] = ""
                updates = edited_df.set_index("JournalId")
                full_df = full_df.set_index("JournalId")
                for col in ["Date", "Tags", "Notes", "Breakeven"]:
                    if col in updates.columns:
                        full_df.loc[updates.index, col] = updates[col]
                full_df = full_df.reset_index()[JOURNAL_SCHEMA]
                _save_journal(full_df)
                st.success("✅ Saved.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

        if col_delete.button("🗑️ Delete Checked Entries", use_container_width=True, type="secondary"):
            state = st.session_state.get(editor_key, None)
            select_map = {}
            if isinstance(state, dict) and "edited_rows" in state:
                for row_pos, changes in state.get("edited_rows", {}).items():
                    try:
                        row_pos = int(row_pos)
                    except Exception:
                        continue
                    if "Select" in changes:
                        select_map[row_pos] = bool(changes["Select"])
            to_delete = []
            for i, row in editor_df.iterrows():
                selected = select_map.get(i, bool(row.get("Select", False)))
                if selected:
                    jid = str(row.get("JournalId", "")).strip()
                    if jid:
                        preview = f"{_clean_str(row.get('Date', ''))} · {_clean_str(row.get('Notes', ''))[:60]}"
                        to_delete.append((jid, preview))
            if not to_delete:
                st.warning("No entries checked. Tick the 🗑 column to select rows.")
            else:
                st.session_state["_pending_delete_ids"] = to_delete
                st.rerun()


# ============================================================
# CONFIRM DELETE DIALOG
# ============================================================

@st.dialog("⚠️ Confirm Delete")
def _confirm_delete_dialog(pending):
    n = len(pending)
    st.markdown(f"You are about to delete **{n} journal {'entry' if n == 1 else 'entries'}**.")
    st.caption("This action cannot be undone.")
    st.markdown("**Entries to delete:**")
    preview_html = (
        "<div style='background:#0E1117; padding:14px; border-left:3px solid #FF6666; "
        "border-radius:6px; max-height:220px; overflow-y:auto;'>"
    )
    for _, preview in pending[:20]:
        preview_html += f"<div style='color:#EEE; font-size:13px; padding:4px 0; line-height:1.4;'>• {_safe_html(preview) or '(empty)'}</div>"
    if len(pending) > 20:
        preview_html += f"<div style='color:#999; font-size:12px; padding-top:8px;'>… and {len(pending) - 20} more</div>"
    preview_html += "</div>"
    st.markdown(preview_html, unsafe_allow_html=True)
    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("✅ Yes, Delete", type="primary", use_container_width=True):
        try:
            ids_to_delete = [jid for jid, _ in pending]
            full_df = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
            remaining = full_df[~full_df["JournalId"].isin(ids_to_delete)]
            _save_journal(remaining)
            st.session_state.pop("_pending_delete_ids", None)
            st.success(f"✅ Deleted {len(ids_to_delete)} entries.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Delete failed: {e}")
    if c2.button("❌ Cancel", use_container_width=True):
        st.session_state.pop("_pending_delete_ids", None)
        st.rerun()


# ============================================================
# CONFIRM CLOSE CYCLE DIALOG
# ============================================================

@st.dialog("🔓 Close Cycle")
def _close_cycle_dialog(campaign_name):
    st.markdown(f"About to close **`{campaign_name}`**")
    st.caption(
        f"Removes `{campaign_name}` from any trade with MULTIPLE tags. "
        f"Trades tagged ONLY with this campaign stay untouched. Its realized "
        f"premium will flow to the parent's Adjusted Cost."
    )
    try:
        trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except Exception:
        st.error("Failed to read trades_history.csv")
        return
    if "Group" not in trades.columns:
        st.error("No Group column found.")
        return

    def _is_shared(g_str):
        groups = _parse_groups(g_str)
        return campaign_name in groups and len(groups) > 1

    affected = trades[trades["Group"].fillna("").apply(_is_shared)]

    if affected.empty:
        st.warning(f"No shared trades for `{campaign_name}`. Nothing to untag.")
        st.markdown("---")
        if st.button("❌ OK", use_container_width=True):
            st.session_state.pop("_pending_close_cycle", None)
            st.rerun()
        return

    st.markdown(f"**{len(affected)} shared trade(s) will be untagged:**")
    preview_html = "<div style='background:#0E1117; padding:14px; border-left:3px solid #FFC300; border-radius:6px; max-height:260px; overflow-y:auto;'>"
    for _, r in affected.head(20).iterrows():
        groups_now = _parse_groups(r.get("Group", ""))
        groups_after = [g for g in groups_now if g != campaign_name]
        after_str = ",".join(groups_after) if groups_after else "(empty)"
        preview_html += (
            f"<div style='color:#EEE; font-size:13px; padding:5px 0; line-height:1.5;'>"
            f"• <b>{_safe_html(_clean_str(r.get('TradeDate', '')))}</b> "
            f"{_safe_html(_clean_str(r.get('Buy/Sell', '')))} "
            f"{_safe_html(_short_desc(r.get('Description', ''), r.get('Symbol', '')))}<br>"
            f"<span style='color:#999; font-size:11px; margin-left:12px;'>"
            f"<code>{_safe_html(','.join(groups_now))}</code> → "
            f"<code style='color:#66FF99;'>{_safe_html(after_str)}</code></span></div>"
        )
    if len(affected) > 20:
        preview_html += f"<div style='color:#999; font-size:12px; padding-top:8px;'>… and {len(affected) - 20} more</div>"
    preview_html += "</div>"
    st.markdown(preview_html, unsafe_allow_html=True)
    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("✅ Yes, Close Cycle", type="primary", use_container_width=True):
        try:
            full_trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
            if "Group" not in full_trades.columns:
                full_trades["Group"] = ""

            def _untag(g_str):
                groups = _parse_groups(g_str)
                if campaign_name not in groups:
                    return g_str
                if len(groups) == 1:
                    return g_str
                return ",".join([g for g in groups if g != campaign_name])

            full_trades["Group"] = full_trades["Group"].fillna("").apply(_untag)
            full_trades.to_csv(TRADES_HISTORY_FILE, index=False)
            st.session_state.pop("_pending_close_cycle", None)
            st.success(f"✅ Closed `{campaign_name}`. {len(affected)} trade(s) untagged.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")
    if c2.button("❌ Cancel", use_container_width=True):
        st.session_state.pop("_pending_close_cycle", None)
        st.rerun()


# ============================================================
# MARK EXPIRED / CLOSED DIALOG  (keeps premium, adds $0 closing leg)
# ============================================================

@st.dialog("🏁 Mark Expired / Closed")
def _mark_expired_dialog(campaign_name):
    st.markdown(f"Mark **`{campaign_name}`** as expired / closed.")
    st.caption(
        "Use when the broker didn't import the closing/expiry trade. Adds a $0 closing leg so the "
        "position nets flat and moves to closed. Your premium and realized stats are PRESERVED."
    )
    try:
        trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
    except Exception:
        st.error("Failed to read trades_history.csv")
        return
    if "Group" not in trades.columns:
        st.error("No Group column found.")
        return

    mask = trades["Group"].fillna("").apply(
        lambda g: campaign_name in _parse_groups(g) and "_ignore" not in _parse_groups(g)
    )
    ct = trades[mask].copy()
    if ct.empty:
        st.warning(f"No trades tagged `{campaign_name}`.")
        if st.button("❌ OK", use_container_width=True):
            st.session_state.pop("_pending_mark_expired", None)
            st.rerun()
        return

    ct["_id"] = ct.apply(_option_identity_from_row, axis=1)
    ct["_sq"] = ct.apply(_signed_qty_row, axis=1)

    open_legs = []
    for idv, sub in ct.groupby("_id"):
        net = float(sub["_sq"].sum())
        if abs(net) > 0.001:
            sub_sorted = sub.copy()
            sub_sorted["_sd"] = pd.to_datetime(sub_sorted["TradeDate"], errors="coerce", dayfirst=True)
            template = sub_sorted.sort_values("_sd", ascending=False).iloc[0]
            open_legs.append((template, net))

    if not open_legs:
        st.info(f"`{campaign_name}` is already flat (no open legs). Nothing to close.")
        if st.button("❌ OK", use_container_width=True):
            st.session_state.pop("_pending_mark_expired", None)
            st.rerun()
        return

    st.markdown(f"**{len(open_legs)} open leg(s) will get a $0 closing trade:**")
    preview_html = "<div style='background:#0E1117; padding:14px; border-left:3px solid #66FF99; border-radius:6px; max-height:260px; overflow-y:auto;'>"
    for template, net in open_legs:
        close_side = "SELL" if net > 0 else "BUY"
        desc = _short_desc(template.get("Description", ""), template.get("Symbol", ""))
        preview_html += (
            f"<div style='color:#EEE; font-size:13px; padding:5px 0; line-height:1.5;'>"
            f"• {_safe_html(desc)} — net <b>{net:+.0f}</b> → "
            f"<span style='color:#66FF99;'>{close_side} {abs(net):.0f} @ &#36;0 {EXPIRED_MARKER}</span></div>"
        )
    preview_html += "</div>"
    st.markdown(preview_html, unsafe_allow_html=True)
    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("✅ Yes, Mark Expired", type="primary", use_container_width=True):
        try:
            full_trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
            today_str = date.today().strftime("%d/%m/%Y")
            new_rows = []
            for template, net in open_legs:
                row = {col: _clean_str(template.get(col, "")) for col in full_trades.columns}
                close_side = "SELL" if net > 0 else "BUY"
                row["Buy/Sell"] = close_side
                row["Quantity"] = str(abs(int(round(net)))) if float(net).is_integer() else str(abs(net))
                row["TradePrice"] = "0"
                row["NetCash"] = "0"
                row["TradeDate"] = today_str
                base_desc = _clean_str(template.get("Description", "")) or _clean_str(template.get("Symbol", ""))
                row["Description"] = f"{base_desc} {EXPIRED_MARKER}".strip()
                row["Group"] = _clean_str(template.get("Group", campaign_name)) or campaign_name
                for rc in ["RealizedPnL", "RealizedPnLSgd"]:
                    if rc in row:
                        row[rc] = "0"
                new_rows.append(row)
            full_trades = pd.concat([full_trades, pd.DataFrame(new_rows)], ignore_index=True)
            full_trades.to_csv(TRADES_HISTORY_FILE, index=False)
            st.session_state.pop("_pending_mark_expired", None)
            st.success(f"✅ Marked `{campaign_name}` expired. {len(new_rows)} closing leg(s) added. Premium preserved.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")
    if c2.button("❌ Cancel", use_container_width=True):
        st.session_state.pop("_pending_mark_expired", None)
        st.rerun()


# ============================================================
# FORCE CLOSE POSITION DIALOG (nuclear → _ignore)
# ============================================================

@st.dialog("🔒 Force Close Position")
def _force_close_dialog(campaign_name):
    st.markdown(f"About to force-close **`{campaign_name}`**")
    st.caption("NUCLEAR option — only for abandoning tracking, delistings, mergers, or bad data.")
    st.warning(
        f"Removes `{campaign_name}` from affected trades and hides them from ALL stats. "
        f"If no other group remains, the trade becomes `_ignore`. "
        f"For a normal expiry, use 🏁 Mark Expired instead (keeps your premium)."
    )
    c1, c2 = st.columns(2)
    if c1.button("✅ Yes, Force Close", type="primary", use_container_width=True):
        try:
            full_trades = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
            if "Group" not in full_trades.columns:
                full_trades["Group"] = ""

            def _force_close_groups(g_str):
                groups = _parse_groups(g_str)
                if campaign_name not in groups:
                    return g_str
                groups = [g for g in groups if g != campaign_name]
                if not groups:
                    groups = ["_ignore"]
                return ",".join(groups)

            n_affected = 0
            for idx in full_trades.index:
                old = full_trades.at[idx, "Group"]
                new = _force_close_groups(old if pd.notna(old) else "")
                if new != old:
                    full_trades.at[idx, "Group"] = new
                    n_affected += 1
            full_trades.to_csv(TRADES_HISTORY_FILE, index=False)
            st.session_state.pop("_pending_force_close", None)
            st.success(f"✅ Force-closed `{campaign_name}`. {n_affected} trade(s) updated.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")
    if c2.button("❌ Cancel", use_container_width=True):
        st.session_state.pop("_pending_force_close", None)
        st.rerun()


# Pop-first pattern: triggering a dialog CONSUMES the pending state immediately,
# so dismissing the modal via the ✕ can't re-open it on the next rerun.
# The dialogs' own Yes/Cancel buttons still call .pop() again (no-op after this),
# so nothing else breaks.
_pop_close_cycle = st.session_state.pop("_pending_close_cycle", None)
if _pop_close_cycle:
    _close_cycle_dialog(_pop_close_cycle)

_pop_mark_expired = st.session_state.pop("_pending_mark_expired", None)
if _pop_mark_expired:
    _mark_expired_dialog(_pop_mark_expired)

_pop_force_close = st.session_state.pop("_pending_force_close", None)
if _pop_force_close:
    _force_close_dialog(_pop_force_close)

_pop_delete_ids = st.session_state.pop("_pending_delete_ids", None)
if _pop_delete_ids:
    _confirm_delete_dialog(_pop_delete_ids)


# ============================================================
# FILE INFO
# ============================================================

with st.expander("ℹ️ File info"):
    st.markdown(f"**Journal storage:** `{TRADE_JOURNAL_FILE}`")
    st.markdown(f"**Journal entries:** {len(journal_df)}")
    st.markdown(f"**Trade rows referenced:** {len(trades_df)}")
    st.markdown(f"**Unique tags:** {len(ALL_TAGS)}")
    if all_groups:
        st.markdown(f"**Campaigns detected:** {len(all_groups)}")
        n_parents = sum(1 for c in campaigns if c["is_parent"])
        n_cycles = sum(1 for c in campaigns if c["is_cycle"])
        n_standalone = sum(1 for c in campaigns if c["is_standalone"])
        st.markdown(f"**Types:** {n_parents} parents · {n_cycles} cycles · {n_standalone} standalone")
    if os.path.exists(TRADE_JOURNAL_FILE):
        size_kb = os.path.getsize(TRADE_JOURNAL_FILE) / 1024
        mtime = datetime.fromtimestamp(os.path.getmtime(TRADE_JOURNAL_FILE))
        st.markdown(f"**File size:** {size_kb:.2f} KB")
        st.markdown(f"**Last modified:** {mtime.strftime('%d/%m/%Y %H:%M:%S')}")
