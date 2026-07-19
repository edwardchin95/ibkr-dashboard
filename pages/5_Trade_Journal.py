import streamlit as st
import pandas as pd
import os
import uuid
from datetime import datetime, date

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

# ⭐ Hide "app" from sidebar
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
    """
    Best-effort ISO YYYY-MM-DD normalization.
    Handles: ISO, ISO with time, DD/M/YYYY, M/D/YYYY, trailing commas, whitespace,
    and truncated dates like '2026-05-'.
    """
    s = _clean_str(s)
    if not s:
        return ""

    # Strip trailing junk: commas, semicolons, extra spaces
    s = s.rstrip(",.;: \t").strip()

    # Take only the date portion if datetime like "2026-05-26 09:31:33"
    if " " in s:
        s = s.split(" ")[0]
    if "T" in s:
        s = s.split("T")[0]

    # Try known formats
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except:
            continue

    # Fallback — pandas is very forgiving
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%Y-%m-%d")
    except:
        pass

    return ""


def _parse_tags(tag_str):
    """Split 'AAOX,AAOX-SP-1,IBKR' into ['AAOX', 'AAOX-SP-1', 'IBKR']."""
    s = _clean_str(tag_str)
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _join_tags(tags_list):
    """Join back to CSV form, deduped, order preserved."""
    seen = set()
    result = []
    for t in tags_list:
        tc = t.strip()
        if tc and tc not in seen:
            seen.add(tc)
            result.append(tc)
    return ",".join(result)


def _safe_html(text):
    """
    ⭐ Escape characters that break Streamlit rendering:
    - `$` → LaTeX math delimiter (biggest culprit)
    - `<`, `>`, `&` → HTML injection / mangling
    """
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("$", "&dollar;")
    )


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

    # ⭐ Normalize dates so sort/join work correctly
    df["Date"] = df["Date"].apply(_normalize_date)

    # ⭐ Runtime dedupe — safety net against messy CSV
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
    """
    Discover campaign groups from:
    - trades_history.csv `Group` column
    - trade_journal.csv `Tags` (tags that match trade Group names or look like campaigns)
    """
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
            # Convention: campaign tags contain a "-" and a digit somewhere
            if "-" in t and any(c.isdigit() for c in t):
                groups.add(t)

    return sorted(groups)


# ============================================================
# CAMPAIGN COMPUTATION
# ============================================================

def _compute_campaign(group_name, trades_df, journal_df):
    # ---- Trades side ----
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

    # ---- Journal side ----
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

    # ---- Unified timeline (trades + notes by date) ----
    timeline = []

    if not gt.empty:
        for _, r in gt.iterrows():
            timeline.append({
                "date": _normalize_date(r.get("TradeDate", "")),
                "kind": "trade",
                "side": _clean_str(r.get("Buy/Sell", "")),
                "symbol": _clean_str(r.get("Symbol", "")),
                "description": _clean_str(r.get("Description", "")) or _clean_str(r.get("Symbol", "")),
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

    # ---- Metadata ----
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

    # Open positions
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

    # BE display
    if c["latest_be"]:
        be_html = (
            "<div>"
            "<div style='color:gray; font-size:12px;'>Latest BE</div>"
            f"<div style='color:#FFC300; font-size:22px; font-weight:bold;'>{_safe_html(c['latest_be'])}</div>"
            "</div>"
        )
    else:
        be_html = ""

    # ⭐ Realized always shown — meaningful for PMCC/CC (closed short calls)
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

    # ⭐ Timeline grouped by date, LATEST FIRST
    events_by_date = {}
    for evt in c["timeline"]:
        d = evt["date"] or "—"
        events_by_date.setdefault(d, []).append(evt)

    timeline_html = ""
    for d in sorted(events_by_date.keys(), reverse=True):
        events = events_by_date[d]
        # Within same date: notes first (context) then trades
        events.sort(key=lambda e: 0 if e["kind"] == "note" else 1)

        # Date header
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

                # Format numbers cleanly
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

                # 2-line layout — mobile-friendly
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
            else:  # note
                be_extra = ""
                if evt["breakeven"]:
                    be_extra = (
                        f"<span style='color:#FFC300; font-size:12px; margin-left:8px; "
                        f"background:rgba(255,195,0,0.15); padding:3px 8px; border-radius:4px; font-weight:bold;'>"
                        f"BE {_safe_html(evt['breakeven'])}"
                        f"</span>"
                    )
                safe_text = _safe_html(evt['text']) or "<i>(empty)</i>"
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

    # ⭐ HTML must NOT be indented — Streamlit markdown treats indented lines as code blocks
    card_html = (
        f"<div class='card' style='padding:20px; border-left:4px solid {border_color}; margin-bottom:16px;'>"
        # ── Header row
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
        # ── Metrics grid
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
        # ── Open positions
        f"<div style='border-top:1px solid #333; padding-top:12px;'>"
        f"<div style='color:gray; font-size:12px; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px;'>Open positions</div>"
        f"{pos_html}"
        f"</div>"
        f"</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)

    with st.expander(f"📜 Timeline · {c['n_trades']} trades · {c['n_notes']} notes"):
        # ⭐ Wrap in dark card so white text is visible against expander's white bg
        wrapped_html = (
            f"<div style='background-color:#0E1117; padding:16px 20px 20px 20px; "
            f"border-radius:6px; margin-top:-8px;'>"
            f"{timeline_html}"
            f"</div>"
        )
        st.markdown(wrapped_html, unsafe_allow_html=True)


# ============================================================
# ADD ENTRY DIALOG
# ============================================================

@st.dialog("➕ Add Journal Entry")
def _add_entry_dialog():
    # ⭐ Text input instead of date_input — date_input inside dialog has a mobile bug
    # where the calendar overlay doesn't dismiss properly
    today_iso = date.today().strftime("%Y-%m-%d")
    entry_date_str = st.text_input(
        "Date (YYYY-MM-DD)",
        value=today_iso,
        help="Format: YYYY-MM-DD (e.g. 2026-07-19)",
    )

    st.markdown("**Tags** (comma-separated)")
    st.caption(
        "Examples: `AAOX,AAOX-SP-1,IBKR,Roll` for a campaign roll · "
        "`MARA,IBKR,plan` for a trade plan · leave empty for general note"
    )

    if ALL_TAGS:
        with st.expander("💡 Existing tags — click to copy"):
            st.markdown(" · ".join([f"`{t}`" for t in ALL_TAGS[:80]]))

    tags_input = st.text_input("Tags", placeholder="AAOX,AAOX-SP-1,IBKR,Roll")

    notes = st.text_area(
        "Notes",
        placeholder="e.g. +$450 credit, total $1450 premium",
        height=120
    )

    breakeven = st.text_input("Breakeven (optional)", placeholder="e.g. 25.50")

    st.markdown("---")

    c1, c2 = st.columns(2)

    if c1.button("✅ Save", type="primary", use_container_width=True):
        if not notes.strip():
            st.error("Notes field cannot be empty.")
            return

        try:
            existing = pd.read_csv(TRADE_JOURNAL_FILE, dtype=str)
        except:
            existing = pd.DataFrame(columns=JOURNAL_SCHEMA)

        for col in JOURNAL_SCHEMA:
            if col not in existing.columns:
                existing[col] = ""

        parsed_tags = _parse_tags(tags_input)
        tags_normalized = _join_tags(parsed_tags)

        # ⭐ Validate the date string user typed (we switched from date_input to text_input)
        parsed_date = _normalize_date(entry_date_str)
        if not parsed_date:
            st.error("Invalid date. Please use YYYY-MM-DD format.")
            return

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

        st.success("✅ Entry saved.")
        st.cache_data.clear()
        st.rerun()

    if c2.button("❌ Cancel", use_container_width=True):
        st.rerun()


# ============================================================
# TOP CONTROLS
# ============================================================

top_c1, _ = st.columns([2, 6])
if top_c1.button("➕ Add Journal Entry", use_container_width=True, type="primary"):
    _add_entry_dialog()


# ============================================================
# SECTION 1 — ACTIVE CAMPAIGNS (auto-discovered)
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
# SECTION 2 — ALL NOTES (editable table with inline delete)
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
        # ⭐ Add inline Select checkbox column for delete
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
                    "🗑",
                    help="Check to mark for delete",
                    default=False,
                    width="small",
                ),
                "Date": st.column_config.TextColumn("Date"),
                "Tags": st.column_config.TextColumn("Tags"),
                "Notes": st.column_config.TextColumn("Notes"),
                "Breakeven": st.column_config.TextColumn("Breakeven"),
                "CreatedAt": st.column_config.TextColumn("Created", disabled=True),
                "JournalId": st.column_config.TextColumn("ID", disabled=True),
            }
        )

        # Style
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

        # ================================
        # SAVE + DELETE buttons
        # ================================
        col_save, col_delete = st.columns([1, 1])

        # SAVE — writes back Date/Tags/Notes/Breakeven edits
        if col_save.button("💾 Save Changes", use_container_width=True, type="primary"):
            try:
                # Pull latest cell edits from session_state
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

                # Normalize edited fields
                for col in ["Date", "Tags", "Notes", "Breakeven"]:
                    if col in edited_df.columns:
                        edited_df[col] = edited_df[col].fillna("").astype(str)
                        edited_df[col] = edited_df[col].replace("nan", "").replace("None", "")

                edited_df["Tags"] = edited_df["Tags"].apply(
                    lambda s: _join_tags(_parse_tags(s))
                )
                edited_df["Date"] = edited_df["Date"].apply(_normalize_date)

                # Merge into full journal by JournalId
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

        # DELETE — reads checkboxes, opens confirmation dialog
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

            # Collect (JournalId, preview) pairs for checked rows
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
                # Stash in session, dialog reads from here
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
    # ⭐ Dark background wrapper — dialog uses white bg, need dark for readable text
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


# Trigger the confirmation dialog if there's a pending delete
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
