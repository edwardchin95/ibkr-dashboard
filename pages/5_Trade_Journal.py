import streamlit as st
import pandas as pd
import os
import uuid
import re
from datetime import datetime, date, timedelta

st.set_page_config(page_title="Trade Journal", page_icon="📝", layout="wide")

from app import (
    require_auth, load_css, DATA_DIR,
    TRADES_HISTORY_FILE,
)


# ============================================================
# AUTH + STYLING
# ============================================================
require_auth()
load_css()

st.markdown("""
<style>
[data-testid="stSidebarNav"] ul li:first-child { display: none; }
</style>
""", unsafe_allow_html=True)

st.title("📝 Trade Journal")
st.caption("Notes, plans, campaign tracking. Independent of broker imports.")


# ============================================================
# CONFIG
# ============================================================

TRADE_JOURNAL_FILE = os.path.join(DATA_DIR, "trade_journal.csv")

JOURNAL_SCHEMA = [
    "JournalId",
    "CreatedAt",
    "Date",
    "Tags",
    "Notes",
    "Breakeven",
]


# ============================================================
# FILE INIT
# ============================================================

def _ensure_journal_file():
    if not os.path.exists(TRADE_JOURNAL_FILE):
        os.makedirs(os.path.dirname(TRADE_JOURNAL_FILE), exist_ok=True)
        pd.DataFrame(columns=JOURNAL_SCHEMA).to_csv(TRADE_JOURNAL_FILE, index=False)


_ensure_journal_file()


# ============================================================
# HELPERS
# ============================================================

def _clean_str(v, default=""):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except:
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
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except:
            continue
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except:
        pass
    return ""


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


def _safe_html(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("$", "&dollar;")
    )


# ============================================================
# Smart trade formatting
# ============================================================

def _format_trade_label(row):
    """
    Get best display label for a trade row.
    - Options (Tiger): extract "(SOFI 20260821 CALL 23.0)" from description
    - Options (IBKR): use description like "AAOX 21JUL26 42 P"
    - Stocks: use description or symbol
    """
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


def _extract_underlying(row):
    """
    Get underlying symbol for tag generation.
    - IBKR:   "AAOX 260717P00042000"       (space-separated)
    - Moomoo: "MARA260724C17500"           (glued)
    - Tiger:  Uses UnderlyingSymbol column
    """
    us = _clean_str(row.get("UnderlyingSymbol", ""))
    if us:
        return us

    sym = _clean_str(row.get("Symbol", ""))
    asset_class = _clean_str(row.get("AssetClass", "")).upper()

    if asset_class == "OPT" and sym:
        if " " in sym:
            return sym.split()[0]

        # Moomoo style: SYMBOL(letters) + YYMMDD + C/P + STRIKE
        m = re.match(r'^([A-Z]+)\d{6}[CP]\d+$', sym.upper())
        if m:
            return m.group(1)

        # Fallback: letters before first digit
        m = re.match(r'^([A-Z]+)', sym.upper())
        if m:
            return m.group(1)

    return sym


def _trade_row_to_tags(row):
    """Clean tag list — underlying + group + platform only. No option code, no strategy."""
    tags = []

    underlying = _extract_underlying(row)
    if underlying:
        tags.append(underlying)

    group = _clean_str(row.get("Group", ""))
    if group and group not in tags:
        tags.append(group)

    platform = _clean_str(row.get("Platform", ""))
    if platform and platform not in tags:
        tags.append(platform)

    return ",".join(tags)


def _trade_row_to_strategy(row):
    return _clean_str(row.get("Strategy", ""))


def _trade_row_to_notes_prefill(row):
    """Build starter note with option identifier + numbers."""
    side = _clean_str(row.get("Buy/Sell", ""))
    label = _format_trade_label(row)
    qty = _clean_str(row.get("Quantity", ""))
    price = _clean_str(row.get("TradePrice", ""))

    try:
        net_f = float(row.get("NetCash", 0))
        net_str = f" ({'+' if net_f >= 0 else ''}${net_f:,.2f})"
    except:
        net_str = ""

    return f"{side} {label} qty {qty} @ {price}{net_str}"


# ============================================================
# LOADERS
# ============================================================

def _get_journal_mtime():
    if os.path.exists(TRADE_JOURNAL_FILE):
        return os.path.getmtime(TRADE_JOURNAL_FILE)
    return 0


def _get_trades_mtime():
    if os.path.exists(TRADES_HISTORY_FILE):
        return os.path.getmtime(TRADES_HISTORY_FILE)
    return 0


@st.cache_data(ttl=300)
def _load_journal(mtime):
    if not os.path.exists(TRADE_JOURNAL_FILE):
        return pd.DataFrame(columns=JOURNAL_SCHEMA)
    try:
        df = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
    except:
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
    except:
        return pd.DataFrame()


def _save_journal(df):
    df = df[JOURNAL_SCHEMA].copy()
    df["Date"] = df["Date"].apply(_normalize_date)
    df.to_csv(TRADE_JOURNAL_FILE, index=False)


# ============================================================
# LOAD DATA
# ============================================================

journal_df = _load_journal(_get_journal_mtime())
trades_df = _load_trades(_get_trades_mtime())


# ============================================================
# TAG DISCOVERY
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
        for g in trades_df["Group"].dropna().unique():
            gc = _clean_str(g)
            if gc:
                groups.add(gc)
    for tag_str in journal_df["Tags"].dropna():
        for t in _parse_tags(tag_str):
            if t in groups:
                continue
            if "-" in t and any(c.isdigit() for c in t):
                groups.add(t)
    return sorted(groups)


# ============================================================
# CAMPAIGN COMPUTATION
# ============================================================

def _compute_campaign(group_name, trades_df, journal_df):
    if not trades_df.empty and "Group" in trades_df.columns:
        gt = trades_df[trades_df["Group"] == group_name].copy()
    else:
        gt = pd.DataFrame()

    net_cash = 0.0
    realized = 0.0
    open_positions = []
    platforms = set()
    strategy_tags = set()

    if not gt.empty:
        net_cash_series = pd.to_numeric(gt.get("NetCash", 0), errors="coerce").fillna(0)
        net_cash = float(net_cash_series.sum())

        if "RealizedPnLSgd" in gt.columns:
            rs = pd.to_numeric(gt["RealizedPnLSgd"], errors="coerce").fillna(0)
        else:
            rs = pd.to_numeric(gt.get("RealizedPnL", 0), errors="coerce").fillna(0)
        realized = float(rs.sum())

        for symbol, sym_df in gt.groupby("Symbol"):
            if not symbol or pd.isna(symbol):
                continue
            qty = pd.to_numeric(sym_df.get("Quantity", 0), errors="coerce").fillna(0).sum()
            if abs(qty) > 0.001:
                latest = sym_df.sort_values("TradeDate", ascending=False).iloc[0]
                open_positions.append({
                    "symbol": symbol,
                    "description": _clean_str(latest.get("Description", "")) or symbol,
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

    matching_notes = []
    latest_be = ""

    for _, r in journal_df.iterrows():
        tags = _parse_tags(r.get("Tags", ""))
        if group_name in tags:
            matching_notes.append({
                "date": _normalize_date(r.get("Date", "")),
                "tags": tags,
                "notes": _clean_str(r.get("Notes", "")),
                "breakeven": _clean_str(r.get("Breakeven", "")),
                "journal_id": _clean_str(r.get("JournalId", "")),
            })

    matching_notes.sort(key=lambda x: x["date"], reverse=True)
    for note in matching_notes:
        if note["breakeven"]:
            latest_be = note["breakeven"]
            break

    timeline = []

    if not gt.empty:
        for _, r in gt.iterrows():
            timeline.append({
                "date": _normalize_date(r.get("TradeDate", "")),
                "kind": "trade",
                "side": _clean_str(r.get("Buy/Sell", "")),
                "symbol": _clean_str(r.get("Symbol", "")),
                "description": _format_trade_label(r),
                "quantity": _clean_str(r.get("Quantity", "")),
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

    timeline.sort(key=lambda x: x["date"] or "0000-00-00")

    all_dates = [t["date"] for t in timeline if t["date"]]
    start_date = min(all_dates) if all_dates else ""
    end_date = max(all_dates) if all_dates else ""

    days_running = 0
    if start_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            days_running = (datetime.now() - sd).days
        except:
            pass

    is_open = len(open_positions) > 0
    n_trades = int(len(gt)) if not gt.empty else 0
    n_notes = len(matching_notes)

    return {
        "group": group_name,
        "platforms": ", ".join(sorted(platforms)) if platforms else "—",
        "strategies": ", ".join(sorted(strategy_tags)) if strategy_tags else "",
        "net_cash": net_cash,
        "realized": realized,
        "open_positions": open_positions,
        "latest_be": latest_be,
        "n_trades": n_trades,
        "n_notes": n_notes,
        "start_date": start_date,
        "end_date": end_date,
        "days_running": days_running,
        "is_open": is_open,
        "timeline": timeline,
    }


# ============================================================
# CAMPAIGN CARD RENDERER
# ============================================================

def _render_campaign_card(c):
    net_color = "#66FF99" if c["net_cash"] >= 0 else "#FF6666"
    real_color = "#66FF99" if c["realized"] >= 0 else "#FF6666"
    border_color = "#00D4FF" if c["is_open"] else "#666"

    pos_html = ""
    if c["open_positions"]:
        for p in c["open_positions"]:
            qty = p["quantity"]
            qty_str = f"{qty:+.0f}" if abs(qty) >= 1 else f"{qty:+.4f}"
            qty_color = "#66FF99" if qty > 0 else "#FF9F1C"
            pos_html += (
                f"<div style='padding:6px 0; display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;'>"
                f"<span style='color:{qty_color}; font-weight:bold; font-size:15px; min-width:40px;'>{qty_str}</span>"
                f"<span style='color:white; font-size:14px;'>{_safe_html(p['description'])}</span>"
                f"</div>"
            )
    else:
        pos_html = "<div style='color:gray; font-size:13px; padding:4px 0;'>All positions closed</div>"

    if c["latest_be"]:
        be_html = (
            "<div>"
            "<div style='color:gray; font-size:12px;'>Latest BE</div>"
            f"<div style='color:#FFC300; font-size:22px; font-weight:bold;'>{_safe_html(c['latest_be'])}</div>"
            "</div>"
        )
    else:
        be_html = ""

    realized_label = "Final Realized" if not c["is_open"] else "Realized"
    realized_tile = (
        f"<div>"
        f"<div style='color:gray; font-size:12px;'>{realized_label}</div>"
        f"<div style='color:{real_color}; font-size:22px; font-weight:bold;'>&dollar;{c['realized']:,.2f}</div>"
        f"</div>"
    )

    status_badge = (
        "<span style='background:rgba(0,212,255,0.15); color:#00D4FF; "
        "padding:2px 8px; border-radius:10px; font-size:10px; font-weight:bold; "
        "margin-left:10px;'>ACTIVE</span>"
    ) if c["is_open"] else (
        "<span style='background:rgba(150,150,150,0.15); color:#999; "
        "padding:2px 8px; border-radius:10px; font-size:10px; font-weight:bold; "
        "margin-left:10px;'>CLOSED</span>"
    )

    events_by_date = {}
    for evt in c["timeline"]:
        d = evt["date"] or "—"
        events_by_date.setdefault(d, []).append(evt)

    timeline_html = ""
    for d in sorted(events_by_date.keys(), reverse=True):
        events = events_by_date[d]
        events.sort(key=lambda e: 0 if e["kind"] == "note" else 1)

        timeline_html += (
            f"<div style='margin-top:20px; margin-bottom:10px; padding-bottom:6px; "
            f"border-bottom:1px solid #444;'>"
            f"<span style='color:#FFC300; font-size:15px; font-weight:bold; letter-spacing:0.5px;'>"
            f"{d}"
            f"</span>"
            f"</div>"
        )

        for evt in events:
            if evt["kind"] == "trade":
                side_color = "#66FF99" if evt["side"] == "SELL" else "#FF9F1C"
                side_bg = "rgba(102,255,153,0.10)" if evt["side"] == "SELL" else "rgba(255,159,28,0.10)"

                try:
                    qty_f = float(evt['quantity'])
                    qty_str = f"{qty_f:+.0f}" if abs(qty_f) >= 1 else f"{qty_f:+.4f}"
                except:
                    qty_str = evt['quantity']
                try:
                    price_f = float(evt['price'])
                    price_str = f"{price_f:,.2f}"
                except:
                    price_str = evt['price']
                try:
                    net_f = float(evt['net_cash'])
                    net_color2 = "#66FF99" if net_f >= 0 else "#FF6666"
                    net_str = f"{'+' if net_f >= 0 else ''}&dollar;{net_f:,.2f}"
                except:
                    net_color2 = "#CCC"
                    net_str = _safe_html(evt['net_cash'])

                timeline_html += (
                    f"<div style='padding:12px 14px; margin:8px 0; "
                    f"background:{side_bg}; border-left:3px solid {side_color}; border-radius:4px;'>"
                    f"<div style='display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:6px;'>"
                    f"<span style='color:{side_color}; font-weight:bold; font-size:14px; letter-spacing:0.5px;'>{_safe_html(evt['side'])}</span>"
                    f"<span style='color:white; font-size:14px; font-weight:600;'>{_safe_html(evt['description'])}</span>"
                    f"</div>"
                    f"<div style='display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap;'>"
                    f"<span style='color:#BBB; font-size:13px;'>{qty_str} contracts @ {price_str}</span>"
                    f"<span style='color:{net_color2}; font-size:15px; font-weight:bold;'>{net_str}</span>"
                    f"</div>"
                    f"</div>"
                )
            else:
                be_extra = ""
                if evt["breakeven"]:
                    be_extra = (
                        f"<span style='color:#FFC300; font-size:12px; margin-left:8px; "
                        f"background:rgba(255,195,0,0.15); padding:3px 8px; border-radius:4px; font-weight:bold;'>"
                        f"BE {_safe_html(evt['breakeven'])}"
                        f"</span>"
                    )
                safe_text = _safe_html(evt['text'])
                if safe_text:
                    safe_text = safe_text.replace("\n", "<br>")
                else:
                    safe_text = "<i>(empty)</i>"
                timeline_html += (
                    f"<div style='padding:12px 14px; margin:8px 0; "
                    f"background:rgba(255,153,204,0.08); border-left:3px solid #FF99CC; border-radius:4px;'>"
                    f"<div style='display:flex; align-items:baseline; gap:8px; margin-bottom:8px; flex-wrap:wrap;'>"
                    f"<span style='color:#FF99CC; font-size:12px; font-weight:bold; letter-spacing:0.5px;'>📝 NOTE</span>"
                    f"{be_extra}"
                    f"</div>"
                    f"<div style='color:#EEE; font-size:14px; line-height:1.5;'>"
                    f"{safe_text}"
                    f"</div>"
                    f"</div>"
                )

    if not timeline_html:
        timeline_html = "<div style='color:gray; font-size:13px;'>No activity yet</div>"

    card_html = (
        f"<div class='card' style='padding:20px; border-left:4px solid {border_color}; margin-bottom:16px;'>"
        f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px; flex-wrap:wrap; gap:8px;'>"
        f"<div>"
        f"<div style='display:flex; align-items:center; flex-wrap:wrap; gap:8px;'>"
        f"<span style='color:white; font-size:22px; font-weight:bold; letter-spacing:0.3px;'>{_safe_html(c['group'])}</span>"
        f"{status_badge}"
        f"</div>"
        f"<div style='color:#999; font-size:13px; margin-top:4px;'>{_safe_html(c['strategies'])} · {_safe_html(c['platforms'])}</div>"
        f"</div>"
        f"<div style='text-align:right;'>"
        f"<div style='color:gray; font-size:11px;'>TRADES / NOTES</div>"
        f"<div style='color:white; font-size:15px; font-weight:bold;'>{c['n_trades']} / {c['n_notes']}</div>"
        f"</div>"
        f"</div>"
        f"<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:18px; margin-bottom:16px;'>"
        f"<div>"
        f"<div style='color:gray; font-size:12px;'>Net Premium</div>"
        f"<div style='color:{net_color}; font-size:22px; font-weight:bold;'>&dollar;{c['net_cash']:,.2f}</div>"
        f"</div>"
        f"{realized_tile}"
        f"{be_html}"
        f"<div>"
        f"<div style='color:gray; font-size:12px;'>Days Running</div>"
        f"<div style='color:white; font-size:20px; font-weight:bold;'>{c['days_running']}</div>"
        f"</div>"
        f"</div>"
        f"<div style='border-top:1px solid #333; padding-top:12px;'>"
        f"<div style='color:gray; font-size:12px; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px;'>Open positions</div>"
        f"{pos_html}"
        f"</div>"
        f"</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)

    with st.expander(f"📜 Timeline · {c['n_trades']} trades · {c['n_notes']} notes"):
        wrapped_html = (
            f"<div style='background-color:#0E1117; padding:16px 20px 20px 20px; "
            f"border-radius:6px; margin-top:-8px;'>"
            f"{timeline_html}"
            f"</div>"
        )
        st.markdown(wrapped_html, unsafe_allow_html=True)


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
        result = result[result["Group"] == group]

    if days > 0 and "TradeDate" in result.columns:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        result = result[result["TradeDate"] >= cutoff]

    result = result.sort_values("TradeDate", ascending=False)
    return result


# ============================================================
# ADD ENTRY DIALOG
# ============================================================

@st.dialog("➕ Add Journal Entry")
def _add_entry_dialog():
    prefill = st.session_state.pop("_journal_prefill", None)

    default_date = date.today().strftime("%Y-%m-%d")
    default_tags = ""
    default_strategy = ""
    default_notes = ""

    if prefill:
        default_date = prefill.get("date") or default_date
        default_tags = prefill.get("tags") or ""
        default_strategy = prefill.get("strategy") or ""
        default_notes = prefill.get("notes") or ""

        st.session_state["dialog_date"] = default_date
        st.session_state["dialog_tags"] = default_tags
        st.session_state["dialog_strategy"] = default_strategy
        st.session_state["dialog_notes"] = default_notes
        st.session_state["dialog_be"] = ""

        # Persist prefill status across reruns
        st.session_state["_dialog_prefill_summary"] = prefill.get("summary", "")

    prefill_summary = st.session_state.get("_dialog_prefill_summary", "")
    if prefill_summary:
        st.success(f"✅ Pre-filled from trade: {prefill_summary}")

    entry_date_str = st.text_input(
        "Date (YYYY-MM-DD)",
        value=default_date,
        key="dialog_date",
    )

    # ============================================================
    # Reference trades — filter + combine button + scrollable list
    # ============================================================
    with st.expander("📊 Reference trades (optional)", expanded=False):

        # Row 1: filter input + combine button
        filter_col, action_col = st.columns([3, 2])

        with filter_col:
            # ⭐ Build symbol dropdown from existing trades (reactive)
            if not trades_df.empty and "Symbol" in trades_df.columns:
                # Get unique underlyings from trade rows
                symbols_set = set()
                for _, tr in trades_df.iterrows():
                    u = _extract_underlying(tr)
                    if u:
                        symbols_set.add(u)
                symbols_list = sorted(symbols_set)
            else:
                symbols_list = []

            options = ["— All / Type below —"] + symbols_list

            selected_symbol = st.selectbox(
                "Filter by symbol",
                options=options,
                key="ref_symbol_select",
                label_visibility="collapsed",
            )

            # Fallback text input for custom filter
            manual_filter = st.text_input(
                "Or type symbol",
                key="ref_symbol_filter",
                placeholder="Type custom symbol...",
                label_visibility="collapsed",
            )

            # Use dropdown if picked, else manual
            if selected_symbol and selected_symbol != "— All / Type below —":
                symbol_filter = selected_symbol
            else:
                symbol_filter = manual_filter

        matching = _filter_trades(trades_df, symbol=symbol_filter, days=365)

        # Count currently-checked trades (before rendering the list)
        selected_count = 0
        for k, v in st.session_state.items():
            if k.startswith("trade_check_") and v is True:
                selected_count += 1

        with action_col:
            if selected_count >= 2:
                if st.button(
                    f"📋 Combine ({selected_count})",
                    type="primary",
                    use_container_width=True,
                    key="combine_btn_top",
                ):
                    selected_trades_data = []
                    for i, (_, r) in enumerate(matching.head(10).iterrows()):
                        if st.session_state.get(f"trade_check_{i}", False):
                            selected_trades_data.append(r)

                    if len(selected_trades_data) >= 2:
                        # Merge tags
                        all_tags = []
                        for tr in selected_trades_data:
                            for t in _parse_tags(_trade_row_to_tags(tr)):
                                if t not in all_tags:
                                    all_tags.append(t)

                        # Strategy — most common
                        strategies = [_trade_row_to_strategy(tr) for tr in selected_trades_data]
                        strategies = [s for s in strategies if s]
                        if strategies:
                            combined_strategy = max(set(strategies), key=strategies.count)
                        else:
                            combined_strategy = ""

                        # Date — most recent
                        dates = [
                            _normalize_date(_clean_str(tr.get("TradeDate", "")))
                            for tr in selected_trades_data
                        ]
                        dates = [d for d in dates if d]
                        combined_date = max(dates) if dates else date.today().strftime("%Y-%m-%d")

                        # Notes — one line per trade + net summary
                        notes_lines = []
                        net_total = 0.0
                        for tr in selected_trades_data:
                            notes_lines.append(_trade_row_to_notes_prefill(tr))
                            try:
                                net_total += float(tr.get("NetCash", 0))
                            except:
                                pass
                        notes_lines.append(f"Net: {'+' if net_total >= 0 else ''}${net_total:,.2f}")
                        combined_notes = "\n".join(notes_lines)

                        # Summary
                        summary_parts = []
                        for tr in selected_trades_data[:2]:
                            s = _clean_str(tr.get("Buy/Sell", ""))
                            lbl = _format_trade_label(tr)
                            summary_parts.append(f"{s} {lbl}")
                        combined_summary = " + ".join(summary_parts)
                        if len(selected_trades_data) > 2:
                            combined_summary += f" (+{len(selected_trades_data) - 2} more)"

                        st.session_state["_journal_prefill"] = {
                            "date": combined_date,
                            "tags": ",".join(all_tags),
                            "strategy": combined_strategy,
                            "notes": combined_notes,
                            "summary": combined_summary,
                        }

                        # Clear checkboxes
                        for k in list(st.session_state.keys()):
                            if k.startswith("trade_check_"):
                                st.session_state.pop(k, None)

                        st.session_state["_reopen_add_dialog"] = True
                        st.rerun()
            else:
                st.caption(f"{selected_count} selected" if selected_count else "Select 2+ to combine")

        # Results count
        if matching.empty:
            if symbol_filter.strip():
                st.caption(f"No matches for '{symbol_filter}'.")
            else:
                st.caption("Type a symbol above to search.")
        else:
            st.caption(f"Showing {min(len(matching), 10)} of {len(matching)} matches")

            # Scrollable container start
            st.markdown(
                "<div style='max-height:400px; overflow-y:auto; padding-right:8px;'>",
                unsafe_allow_html=True,
            )

            for i, (_, r) in enumerate(matching.head(10).iterrows()):
                trade_date_raw = _clean_str(r.get("TradeDate", ""))
                trade_date = trade_date_raw.split(" ")[0] if " " in trade_date_raw else trade_date_raw

                side = _clean_str(r.get("Buy/Sell", ""))
                platform = _clean_str(r.get("Platform", ""))
                label = _format_trade_label(r)
                qty = _clean_str(r.get("Quantity", ""))
                price = _clean_str(r.get("TradePrice", ""))

                try:
                    net_f = float(r.get("NetCash", 0))
                    net_display = f"{'+' if net_f >= 0 else ''}${net_f:,.0f}"
                    net_color = "#0A7B3E" if net_f >= 0 else "#DC2626"
                except:
                    net_display = ""
                    net_color = "#666"

                side_color = "#0A7B3E" if side == "SELL" else "#D97706"

                with st.container(border=True):
                    ck_col, info_col, act_col = st.columns([0.7, 5, 1])

                    with ck_col:
                        st.checkbox(
                            "sel",
                            key=f"trade_check_{i}",
                            label_visibility="collapsed",
                        )

                    with info_col:
                        st.markdown(
                            f"<div style='font-size:11px; color:#888;'>"
                            f"{trade_date} · {platform}"
                            f"</div>"
                            f"<div style='margin-top:2px;'>"
                            f"<span style='color:{side_color}; font-weight:700; font-size:13px;'>{_safe_html(side)}</span> "
                            f"<span style='font-size:13px;'>{_safe_html(label)}</span>"
                            f"</div>"
                            f"<div style='font-size:11px; color:#666; margin-top:2px;'>"
                            f"qty {_safe_html(qty)} @ {_safe_html(price)}"
                            f" · <span style='color:{net_color}; font-weight:600;'>{net_display}</span>"
                            f"</div>",
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

            # Scrollable container end
            st.markdown("</div>", unsafe_allow_html=True)

    # ============================================================
    # Tags, Strategy, Notes, BE
    # ============================================================
    st.markdown("**Tags** (comma-separated)")

    if ALL_TAGS:
        with st.expander("💡 Existing tags"):
            st.markdown(" · ".join([f"`{t}`" for t in ALL_TAGS[:80]]))

    tags_input = st.text_input(
        "Tags",
        value=default_tags,
        placeholder="AAOX,AAOX-SP-1,IBKR",
        key="dialog_tags",
    )

    strategy_input = st.text_input(
        "Strategy",
        value=default_strategy,
        placeholder="e.g. SP, CC, PMCC, Roll Out and Down",
        help="Added as a tag on save (auto-populated when copying from trade)",
        key="dialog_strategy",
    )

    notes = st.text_area(
        "Notes",
        value=default_notes,
        placeholder="e.g. +$450 credit, total $1450 premium",
        height=120,
        key="dialog_notes",
    )

    breakeven = st.text_input(
        "Breakeven (optional)",
        placeholder="e.g. 25.50",
        key="dialog_be",
    )

    st.markdown("---")

    c1, c2 = st.columns(2)

    if c1.button("✅ Save", type="primary", use_container_width=True):
        if not notes.strip():
            st.error("Notes field cannot be empty.")
            return

        parsed_date = _normalize_date(entry_date_str)
        if not parsed_date:
            st.error("Invalid date. Please use YYYY-MM-DD format.")
            return

        try:
            existing = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
        except:
            existing = pd.DataFrame(columns=JOURNAL_SCHEMA)

        for col in JOURNAL_SCHEMA:
            if col not in existing.columns:
                existing[col] = ""

        # Merge Strategy field into Tags on save
        parsed_tags = _parse_tags(tags_input)
        strategy_clean = strategy_input.strip()
        if strategy_clean and strategy_clean not in parsed_tags:
            parsed_tags.append(strategy_clean)

        tags_normalized = _join_tags(parsed_tags)

        new_row = {
            "JournalId": str(uuid.uuid4()),
            "CreatedAt": datetime.now().isoformat(),
            "Date": parsed_date,
            "Tags": tags_normalized,
            "Notes": notes.strip(),
            "Breakeven": breakeven.strip(),
        }

        new_df = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
        _save_journal(new_df)

        # Clean up widget state so next open starts fresh
        for k in ["dialog_date", "dialog_tags", "dialog_strategy", "dialog_notes", "dialog_be"]:
            st.session_state.pop(k, None)
        st.session_state.pop("_dialog_prefill_summary", None)
        for k in list(st.session_state.keys()):
            if k.startswith("trade_check_"):
                st.session_state.pop(k, None)

        st.success("✅ Entry saved.")
        st.cache_data.clear()
        st.rerun()

    if c2.button("❌ Cancel", use_container_width=True):
        for k in ["dialog_date", "dialog_tags", "dialog_strategy", "dialog_notes", "dialog_be"]:
            st.session_state.pop(k, None)
        st.session_state.pop("_dialog_prefill_summary", None)
        for k in list(st.session_state.keys()):
            if k.startswith("trade_check_"):
                st.session_state.pop(k, None)
        st.rerun()


# ============================================================
# AUTO-REOPEN DIALOG after Copy click
# ============================================================

if st.session_state.pop("_reopen_add_dialog", False):
    _add_entry_dialog()


# ============================================================
# TOP CONTROLS
# ============================================================

top_c1, _ = st.columns([2, 6])
if top_c1.button("➕ Add Journal Entry", use_container_width=True, type="primary"):
    _add_entry_dialog()


# ============================================================
# SECTION 1 — ACTIVE CAMPAIGNS
# ============================================================

st.markdown(
    "<div class='section-title'>📊 Active Campaigns</div>",
    unsafe_allow_html=True
)

all_groups = _get_all_campaign_groups(journal_df, trades_df)

if not all_groups:
    st.info("No campaigns yet. Add a journal entry with a campaign tag (e.g. `AAOX-SP-1`) or upload broker CSV with Group column.")
else:
    campaigns = [_compute_campaign(g, trades_df, journal_df) for g in all_groups]

    active = [c for c in campaigns if c["is_open"]]
    closed = [c for c in campaigns if not c["is_open"]]

    active.sort(key=lambda x: x["days_running"], reverse=True)
    closed.sort(key=lambda x: x["end_date"], reverse=True)

    if active:
        for c in active:
            _render_campaign_card(c)
    else:
        st.caption("No active campaigns. All groups fully closed.")

    if closed:
        with st.expander(f"📁 Closed Campaigns ({len(closed)})"):
            for c in closed:
                _render_campaign_card(c)

st.markdown("---")


# ============================================================
# SECTION 2 — RECENT TRADES BROWSER
# ============================================================

st.markdown(
    "<div class='section-title'>📊 Recent Trades</div>",
    unsafe_allow_html=True
)

if trades_df.empty:
    st.info("No trades in trades_history.csv")
else:
    with st.expander("Browse trades to reference", expanded=False):
        b_c1, b_c2, b_c3, b_c4 = st.columns(4)

        with b_c1:
            platforms_list = ["All"] + sorted(trades_df["Platform"].dropna().unique().tolist())
            browse_platform = st.selectbox("Broker", platforms_list, key="browse_platform")

        with b_c2:
            browse_symbol = st.text_input("Symbol", key="browse_symbol", placeholder="e.g. AAOX")

        with b_c3:
            if "Group" in trades_df.columns:
                groups_list = ["All"] + sorted(
                    [g for g in trades_df["Group"].dropna().unique().tolist() if _clean_str(g)]
                )
            else:
                groups_list = ["All"]
            browse_group = st.selectbox("Group", groups_list, key="browse_group")

        with b_c4:
            browse_days = st.selectbox(
                "Time range",
                [30, 90, 180, 365, 9999],
                format_func=lambda x: {30: "30 days", 90: "90 days", 180: "180 days", 365: "1 year", 9999: "All"}.get(x, str(x)),
                index=2,
                key="browse_days",
            )

        browse_result = _filter_trades(
            trades_df,
            symbol=browse_symbol,
            platform=browse_platform,
            group=browse_group,
            days=browse_days,
        )

        st.caption(f"Showing {len(browse_result)} trade(s)")

        if browse_result.empty:
            st.info("No trades match filters.")
        else:
            display_cols = [
                c for c in [
                    "TradeDate", "Platform", "Symbol", "Description", "AssetClass",
                    "Buy/Sell", "Quantity", "TradePrice", "NetCash",
                    "RealizedPnLSgd", "Strategy", "Group",
                ] if c in browse_result.columns
            ]
            st.dataframe(
                browse_result[display_cols].head(200),
                use_container_width=True,
                hide_index=True,
            )

st.markdown("---")


# ============================================================
# SECTION 3 — ALL NOTES (editable table with inline delete)
# ============================================================

st.markdown(
    "<div class='section-title'>📝 All Notes</div>",
    unsafe_allow_html=True
)

if journal_df.empty:
    st.info("No journal entries yet.")
else:
    filter_c1, filter_c2 = st.columns([2, 3])

    with filter_c1:
        selected_tags = st.multiselect(
            "Filter by tags (AND)",
            options=ALL_TAGS,
            help="Show entries that contain ALL selected tags",
        )

    with filter_c2:
        search_text = st.text_input(
            "Search in notes",
            placeholder="Substring search on Notes field"
        )

    filtered = journal_df.copy()

    if selected_tags:
        def has_all_tags(tag_str):
            entry_tags = set(_parse_tags(tag_str))
            return all(t in entry_tags for t in selected_tags)

        filtered = filtered[filtered["Tags"].apply(has_all_tags)]

    if search_text.strip():
        search_query = search_text.strip()
        filtered = filtered[
            filtered["Notes"].fillna("").str.contains(search_query, case=False, na=False)
        ]

    filtered = filtered.sort_values("Date", ascending=False)

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
            editor_df,
            use_container_width=True,
            hide_index=True,
            disabled=["CreatedAt", "JournalId"],
            key=editor_key,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "🗑", help="Check to mark for delete", default=False, width="small",
                ),
                "Date": st.column_config.TextColumn("Date"),
                "Tags": st.column_config.TextColumn("Tags"),
                "Notes": st.column_config.TextColumn("Notes"),
                "Breakeven": st.column_config.TextColumn("Breakeven"),
                "CreatedAt": st.column_config.TextColumn("Created", disabled=True),
                "JournalId": st.column_config.TextColumn("ID", disabled=True),
            }
        )

        st.markdown("""
        <style>
        div[data-testid="stDataEditor"] table { width: 100% !important; }
        div[data-testid="stDataEditor"] th,
        div[data-testid="stDataEditor"] td {
            white-space: normal !important;
            max-width: none !important;
        }
        div[data-testid="stDataEditor"] textarea {
            white-space: pre-wrap !important;
            overflow-wrap: break-word !important;
            word-break: break-word !important;
            min-height: 60px !important;
            line-height: 1.5;
        }
        </style>
        """, unsafe_allow_html=True)

        col_save, col_delete = st.columns([1, 1])

        if col_save.button("💾 Save Changes", use_container_width=True, type="primary"):
            try:
                state = st.session_state.get(editor_key, None)
                if isinstance(state, dict) and "edited_rows" in state:
                    edited_df = edited_df.copy()
                    for row_pos, changes in state.get("edited_rows", {}).items():
                        try:
                            row_pos = int(row_pos)
                        except:
                            continue
                        for col, value in changes.items():
                            if col in edited_df.columns and row_pos < len(edited_df):
                                edited_df.iloc[
                                    row_pos,
                                    edited_df.columns.get_loc(col)
                                ] = value

                for col in ["Date", "Tags", "Notes", "Breakeven"]:
                    if col in edited_df.columns:
                        edited_df[col] = edited_df[col].fillna("").astype(str)
                        edited_df[col] = edited_df[col].replace("nan", "").replace("None", "")

                edited_df["Tags"] = edited_df["Tags"].apply(
                    lambda s: _join_tags(_parse_tags(s))
                )
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
                    except:
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
        preview_html += (
            f"<div style='color:#EEE; font-size:13px; padding:4px 0; line-height:1.4;'>"
            f"• {_safe_html(preview) or '(empty)'}"
            f"</div>"
        )
    if len(pending) > 20:
        preview_html += (
            f"<div style='color:#999; font-size:12px; padding-top:8px;'>"
            f"… and {len(pending) - 20} more"
            f"</div>"
        )
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


if st.session_state.get("_pending_delete_ids"):
    _confirm_delete_dialog(st.session_state["_pending_delete_ids"])


# ============================================================
# FILE INFO
# ============================================================

with st.expander("ℹ️ File info"):
    st.markdown(f"**Journal storage:** `{TRADE_JOURNAL_FILE}`")
    st.markdown(f"**Journal entries:** {len(journal_df)}")
    st.markdown(f"**Trade rows referenced:** {len(trades_df)}")
    st.markdown(f"**Unique tags:** {len(ALL_TAGS)}")
    if 'all_groups' in dir():
        st.markdown(f"**Campaigns detected:** {len(all_groups)}")

    if os.path.exists(TRADE_JOURNAL_FILE):
        size_kb = os.path.getsize(TRADE_JOURNAL_FILE) / 1024
        mtime = datetime.fromtimestamp(os.path.getmtime(TRADE_JOURNAL_FILE))
        st.markdown(f"**File size:** {size_kb:.2f} KB")
        st.markdown(f"**Last modified:** {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
