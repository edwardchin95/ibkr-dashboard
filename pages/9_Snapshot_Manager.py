import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime, timedelta

from app import (
    require_auth,
    load_css,
    get_history_file,
    get_trades_history_file,
    get_snapshot_dir,
    detect_coverage_gaps,
    detect_platform,
    _existing_snapshot_files,
    _find_overlap,
    delete_snapshot,
    _get_open_trading_days,
)

from ibkr import (
    get_ibkr_snapshot_dir,
    save_snapshot_and_history as save_ibkr_snapshot,
    extract_nav_cash as ibkr_extract_nav_cash,
    extract_total_pnl as ibkr_extract_total_pnl,
    extract_total_deposit as ibkr_extract_total_deposit,
)

from tiger import (
    get_tiger_snapshot_dir,
    save_snapshot_and_history as save_tiger_snapshot,
)

from moomoo import (
    get_moomoo_snapshot_dir,
    save_snapshot_and_history as save_moomoo_snapshot,
)


# ============================================================
# PAGE SETUP
# ============================================================

st.set_page_config(page_title="Snapshot Manager", page_icon="🗂️", layout="wide")

require_auth()
load_css()

# Hide root app + basic styling
st.markdown("""
<style>
[data-testid="stSidebarNav"] ul li:first-child { display: none; }
.block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)


st.title("🗂️ Snapshot Manager")
st.caption("Manage uploaded statements, review coverage, and delete accidental uploads.")


# ============================================================
# CONFIG
# ============================================================

PLATFORMS = {
    "IBKR": get_ibkr_snapshot_dir,
    "Tiger": get_tiger_snapshot_dir,
    "Moomoo": get_moomoo_snapshot_dir,
}

# Snapshots whose START DATE falls on or after this cutoff are guaranteed
# to be correctly recorded with a SnapshotFile column in trades_history.csv
# (schema change took effect from this date onward). Anything before this
# cutoff may not be fully/consistently tagged, so it is locked (read-only).
DELETE_CUTOFF = datetime(2026, 7, 16)


# ============================================================
# HELPERS
# ============================================================

def _load_history():
    if not os.path.exists(get_history_file()):
        return pd.DataFrame()
    try:
        return pd.read_csv(get_history_file())
    except:
        return pd.DataFrame()


def _load_trades():
    if not os.path.exists(get_trades_history_file()):
        return pd.DataFrame()
    try:
        return pd.read_csv(get_trades_history_file(), dtype=str)
    except:
        return pd.DataFrame()


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


def _extract_date_range(snapshot_name):
    m = re.search(r"\((\d{8})-(\d{8})\)", str(snapshot_name))
    if not m:
        return "", ""
    s = m.group(1)
    e = m.group(2)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}", f"{e[:4]}-{e[4:6]}-{e[6:8]}"


def _pretty_date(iso_date):
    if not iso_date:
        return "Unknown"
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except:
        return iso_date


def _extract_end_year(snapshot_name):
    _, end = _extract_date_range(snapshot_name)
    return end[:4] if end else "Unknown"


def _sort_key(snapshot_name):
    _, end = _extract_date_range(snapshot_name)
    return end or "0000-00-00"

def _get_next_upload_date(platform, history_df):
    """
    Find the latest END date across this platform's snapshots (read from
    the SnapshotFile names) and return the NEXT day as an ISO string.

    This is what the user should set as the START date of their next
    statement export. Returns "" if no dated snapshot exists.
    """
    if history_df is None or history_df.empty:
        return ""
    if "Platform" not in history_df.columns or "SnapshotFile" not in history_df.columns:
        return ""

    sub = history_df[history_df["Platform"].astype(str) == platform]
    if sub.empty:
        return ""

    latest_end = ""
    for name in sub["SnapshotFile"].astype(str):
        _, end = _extract_date_range(name)
        if end and end > latest_end:
            latest_end = end

    if not latest_end:
        return ""

    try:
        end_dt = datetime.strptime(latest_end, "%Y-%m-%d")
        # ⭐ Advance to the next TRADING day (NYSE or SGX open),
        # skipping weekends + US/SG holidays.
        candidate = end_dt + timedelta(days=1)
        open_days = _get_open_trading_days(candidate, candidate + timedelta(days=10))
        future_open = sorted(d for d in open_days if d >= candidate.date())
        if future_open:
            return future_open[0].strftime("%Y-%m-%d")
        return candidate.strftime("%Y-%m-%d")
    except:
        return ""

def _advance_one_trading_day(iso_date):
    """Return the next trading day (NYSE or SGX open) strictly after iso_date."""
    if not iso_date:
        return ""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        candidate = d + timedelta(days=1)
        open_days = _get_open_trading_days(candidate, candidate + timedelta(days=10))
        future_open = sorted(x for x in open_days if x >= candidate.date())
        if future_open:
            return future_open[0].strftime("%Y-%m-%d")
        return candidate.strftime("%Y-%m-%d")
    except:
        return iso_date
    
def _count_impact(snapshot_name, platform, history_df, trades_df):
    portfolio_rows = 0
    trade_rows = 0

    if not history_df.empty and "SnapshotFile" in history_df.columns and "Platform" in history_df.columns:
        portfolio_rows = int(
            (
                (history_df["Platform"].astype(str) == platform)
                & (history_df["SnapshotFile"].astype(str) == snapshot_name)
            ).sum()
        )

    if not trades_df.empty and "SnapshotFile" in trades_df.columns and "Platform" in trades_df.columns:
        trade_rows = int(
            (
                (trades_df["Platform"].astype(str) == platform)
                & (trades_df["SnapshotFile"].astype(str) == snapshot_name)
            ).sum()
        )

    return portfolio_rows, trade_rows


def _is_deletable(snapshot_name):
    """
    Allow deletion only for snapshots whose START DATE is on or after
    16 Jul 2026 — this is the first snapshot correctly recorded with
    SnapshotFile in trades_history.csv under the new schema. Snapshots
    starting before this cutoff are locked (read-only), since older
    trade rows may not have a reliable SnapshotFile linkage.
    """
    start, _ = _extract_date_range(snapshot_name)
    if not start:
        return False
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        return start_dt >= DELETE_CUTOFF
    except:
        return False


def _delete_snapshot(platform, snapshot_name):
    """Thin wrapper — delegates to the shared delete in app.py.
    Returns (ok, error)."""
    ok, err = delete_snapshot(platform, snapshot_name)
    if ok:
        try:
            st.cache_data.clear()
        except:
            pass
    return ok, err


def _save_uploaded_file(platform, uploaded_file):
    """
    Route an uploaded file to the correct broker save function.
    Mirrors overview.py behaviour (IBKR needs pre-extracted nav/cash/pnl/deposit).
    """
    if platform == "IBKR":
        uploaded_file.seek(0)
        total_nav, cash_v, _, _ = ibkr_extract_nav_cash(uploaded_file)

        uploaded_file.seek(0)
        pnl_v = ibkr_extract_total_pnl(uploaded_file)

        uploaded_file.seek(0)
        deposit_raw = ibkr_extract_total_deposit(uploaded_file)

        uploaded_file.seek(0)
        save_ibkr_snapshot(uploaded_file, total_nav, cash_v, pnl_v, deposit_raw)

    elif platform == "Tiger":
        uploaded_file.seek(0)
        save_tiger_snapshot(uploaded_file)

    elif platform == "TigerPDF":
        uploaded_file.seek(0)
        from tiger_pdf import save_snapshot_and_history as save_tigerpdf_snapshot
        save_tigerpdf_snapshot(uploaded_file)

    elif platform == "Moomoo":
        uploaded_file.seek(0)
        save_moomoo_snapshot(uploaded_file)

# ============================================================
# UPLOAD STATEMENTS (auto-process, no button — like Overview)
# ============================================================

st.markdown("## 📤 Upload Statements")

# The uploader's key changes after each processing run. Bumping the key
# resets the widget to empty, so (a) the processed file disappears from
# the uploader, and (b) there is no session "memory" blocking a re-upload
# of the same file after you delete it.
if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

uploaded_files = st.file_uploader(
    "Upload statement files",
    type=["csv", "pdf"],
    accept_multiple_files=True,
    key=f"stmt_uploader_{st.session_state['uploader_key']}",
    help="IBKR, Tiger, Moomoo CSV + Tiger PDF (mobile/web) are supported. Files are processed automatically."
)

if uploaded_files:

    results = []  # list of (filename, status, message)
    any_new = False

    for uploaded_file in uploaded_files:

        raw = uploaded_file.getvalue()
        platform = detect_platform(raw)

        if platform not in ("IBKR", "Tiger", "TigerPDF", "Moomoo"):
            results.append((uploaded_file.name, "error", "Unsupported statement format"))
            continue

        # TigerPDF saves rows as "Tiger" — normalize for history/overlap/delete
        hist_platform = "Tiger" if platform == "TigerPDF" else platform

        # Snapshot list BEFORE saving — used for duplicate/overlap detection
        before = _existing_snapshot_files(hist_platform)

        try:
            _save_uploaded_file(platform, uploaded_file)
        except Exception as e:
            results.append((uploaded_file.name, "error", f"{platform} · {str(e)}"))
            continue

        after = _existing_snapshot_files(hist_platform)
        new_files = after - before

        if new_files:
            new_name = sorted(new_files)[0]

            # ⭐ Overlap guard: if the newly-saved range overlaps an existing
            # statement by 2+ days, roll it back (delete) and warn the user.
            overlap_with = _find_overlap(new_name, before)

            if overlap_with:
                _delete_snapshot(hist_platform, new_name)
                results.append(
                    (uploaded_file.name, "overlap",
                     f"{platform} · range **{new_name}** overlaps existing "
                     f"**{overlap_with}**. Upload rejected — export a "
                     f"non-overlapping range instead.")
                )
            else:
                any_new = True
                results.append(
                    (uploaded_file.name, "success",
                     f"{platform} · saved as **{new_name}**")
                )
        else:
            # Same filename/date range already exists → nothing new was added
            results.append(
                (uploaded_file.name, "duplicate",
                 f"{platform} · already uploaded / date range already covered")
            )

    if any_new:
        st.cache_data.clear()

    # Stash results, reset the uploader (empties the widget), and rerun so
    # the page reflects the new snapshot immediately.
    st.session_state["last_upload_results"] = results
    st.session_state["uploader_key"] += 1
    st.rerun()

# Show the outcome of the most recent upload (survives the reset rerun above)
if st.session_state.get("last_upload_results"):
    for name, status, msg in st.session_state["last_upload_results"]:
        if status == "success":
            st.success(f"✅ **{name}** processed — {msg}")
        elif status == "duplicate":
            st.info(f"ℹ️ **{name}** — {msg}")
        elif status == "overlap":
            st.warning(f"⛔ **{name}** — {msg}")
        else:
            st.error(f"❌ **{name}** — {msg}")

st.markdown("---")


# ============================================================
# COVERAGE RENDERER — uses native Streamlit metric (no HTML)
# ============================================================

def _render_coverage_card(platform, history_df):
    coverage_info = detect_coverage_gaps(platform)

    if not coverage_info.get("ranges"):
        return

    n_statements = len(coverage_info["ranges"])
    covered = coverage_info.get("covered_days", 0)
    total = coverage_info.get("total_days", 0)
    n_gaps = len(coverage_info.get("gaps", []))
    n_overlaps = len(coverage_info.get("overlaps", []))

    with st.container(border=True):
        st.markdown(f"**{platform}**")

        next_upload = _get_next_upload_date(platform, history_df)
        if next_upload:
            if platform == "IBKR":
                # ⭐ IBKR export includes the trading day BEFORE the selected
                # "from" date. So to actually capture activity from `next_upload`,
                # the user must select one trading day later.
                ibkr_select = _advance_one_trading_day(next_upload)
                st.info(
                    f"📌 Next coverage starts **{_pretty_date(next_upload)}** — "
                    f"but in IBKR export, select **From = {_pretty_date(ibkr_select)}** "
                    f"(IBKR includes the prior trading day)."
                )
            else:
                st.info(f"📌 Next statement should start from **{_pretty_date(next_upload)}**")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Statements", n_statements)
        c2.metric("Coverage", f"{covered}/{total} days")

        if n_gaps == 0:
            c3.metric("Gaps", "0 ✅")
        else:
            c3.metric("Gaps", f"{n_gaps} ⚠️")

        if n_overlaps == 0:
            c4.metric("Overlaps", "0 ✅")
        else:
            c4.metric("Overlaps", f"{n_overlaps} ⚠️")

        # Details only when problems exist
        if n_gaps > 0:
            with st.expander(f"⚠️ Missing date ranges ({n_gaps})"):
                for gs, ge in coverage_info["gaps"]:
                    st.markdown(f"- `{gs}` → `{ge}`")

        if n_overlaps > 0:
            with st.expander(f"⚠️ Overlapping ranges ({n_overlaps})"):
                for os_, oe_ in coverage_info["overlaps"]:
                    st.markdown(f"- `{os_}` → `{oe_}`")


# ============================================================
# SNAPSHOT CARD RENDERER — flat HTML string, no indentation
# ============================================================

def _render_snapshot_card(range_title, snapshot_name, portfolio_rows, trade_rows, deletable):
    """
    Render one snapshot card. Uses single-line concatenated HTML
    to avoid Streamlit markdown treating indented lines as code blocks.
    """
    status_color = "#66FF99" if deletable else "#9CA3AF"
    status_text = "Deletable" if deletable else "Locked"

    lock_html = ""
    if not deletable:
        lock_html = (
            "<div style='color:#9CA3AF; font-size:13px; margin-top:8px;'>"
            "🔒 Legacy snapshot, read-only"
            "</div>"
        )

    html = (
        "<div style='background:#0E1117; border:1px solid #2D3748; "
        "border-radius:12px; padding:16px; margin-bottom:12px;'>"
        f"<div style='color:white; font-size:17px; font-weight:700; line-height:1.35; word-break:break-word;'>{range_title}</div>"
        f"<div style='color:#9CA3AF; font-size:12px; margin-top:6px; word-break:break-word;'>{snapshot_name}</div>"
        "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(100px, 1fr)); gap:14px; margin-top:16px;'>"
        "<div>"
        "<div style='color:#9CA3AF; font-size:11px; text-transform:uppercase; letter-spacing:0.5px;'>Portfolio</div>"
        f"<div style='color:white; font-size:16px; font-weight:700;'>{portfolio_rows}</div>"
        "</div>"
        "<div>"
        "<div style='color:#9CA3AF; font-size:11px; text-transform:uppercase; letter-spacing:0.5px;'>Trades</div>"
        f"<div style='color:white; font-size:16px; font-weight:700;'>{trade_rows}</div>"
        "</div>"
        "<div>"
        "<div style='color:#9CA3AF; font-size:11px; text-transform:uppercase; letter-spacing:0.5px;'>Status</div>"
        f"<div style='color:{status_color}; font-size:16px; font-weight:700;'>{status_text}</div>"
        "</div>"
        "</div>"
        f"{lock_html}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# ============================================================
# LOAD DATA
# ============================================================

history_df = _load_history()
trades_df = _load_trades()


# ============================================================
# COVERAGE OVERVIEW
# ============================================================

st.markdown("## 📊 Coverage Overview")

for platform in PLATFORMS.keys():
    _render_coverage_card(platform, history_df)

st.markdown("---")


# ============================================================
# DELETE CONFIRMATION DIALOG
# ============================================================

@st.dialog("⚠️ Confirm Delete")
def _confirm_delete_dialog(platform, snapshot_name):
    portfolio_rows, trade_rows = _count_impact(
        snapshot_name, platform, history_df, trades_df
    )
    start, end = _extract_date_range(snapshot_name)

    st.markdown(f"Delete snapshot from **{platform}**?")
    st.markdown(f"**Date range:** `{start or 'Unknown'}` → `{end or 'Unknown'}`")
    st.markdown(f"**File:** `{snapshot_name}`")

    st.warning(
        f"This will remove {portfolio_rows} portfolio row(s), "
        f"{trade_rows} trade row(s), and the physical snapshot file."
    )

    c1, c2 = st.columns(2)

    if c1.button("✅ Yes, Delete", type="primary", width="stretch"):
        ok, err = _delete_snapshot(platform, snapshot_name)
        if ok:
            st.session_state.pop("_pending_delete_snapshot", None)
            st.session_state.pop("last_upload_results", None)
            st.success("✅ Snapshot deleted.")
            st.rerun()
        else:
            st.error(f"⛔ Delete failed — {err}")

    if c2.button("❌ Cancel", width="stretch"):
        st.session_state.pop("_pending_delete_snapshot", None)
        st.rerun()


if st.session_state.get("_pending_delete_snapshot"):
    p, s = st.session_state["_pending_delete_snapshot"]
    _confirm_delete_dialog(p, s)


# ============================================================
# SNAPSHOT CARDS
# ============================================================

st.markdown("## 📁 Snapshots")


def _render_one_snapshot(platform, r):
    """Render a single snapshot card + its delete button (if deletable)."""
    snapshot_name = _clean_str(r.get("SnapshotFile", ""))
    if not snapshot_name:
        return

    start, end = _extract_date_range(snapshot_name)
    start_pretty = _pretty_date(start)
    end_pretty = _pretty_date(end)

    portfolio_rows, trade_rows = _count_impact(
        snapshot_name, platform, history_df, trades_df
    )
    deletable = _is_deletable(snapshot_name)

    range_title = (
        f"{start_pretty} → {end_pretty}"
        if start and end
        else "Unknown date range"
    )

    _render_snapshot_card(
        range_title,
        snapshot_name,
        portfolio_rows,
        trade_rows,
        deletable,
    )

    if deletable:
        if st.button(
            "🗑 Delete this snapshot",
            key=f"delete_{platform}_{snapshot_name}",
            width="stretch",
            type="secondary",
        ):
            st.session_state["_pending_delete_snapshot"] = (
                platform,
                snapshot_name,
            )
            st.rerun()

    st.markdown("")


if history_df.empty or "SnapshotFile" not in history_df.columns or "Platform" not in history_df.columns:
    st.info("No snapshot history found.")
else:
    for platform, snap_dir in PLATFORMS.items():
        st.markdown(f"### {platform}")

        sub = history_df[history_df["Platform"].astype(str) == platform].copy()

        if sub.empty:
            st.caption("No snapshots yet.")
            st.markdown("---")
            continue

        sub = sub.drop_duplicates(subset=["SnapshotFile"], keep="last")
        sub["_sort_key"] = sub["SnapshotFile"].apply(_sort_key)
        sub = sub.sort_values("_sort_key", ascending=False)

        # ⭐ Latest snapshot shown directly (always visible, auto-expanded).
        latest_row = sub.iloc[0]
        st.markdown("**🆕 Latest**")
        _render_one_snapshot(platform, latest_row)

        # ⭐ Everything else grouped by year, all collapsed by default.
        rest = sub.iloc[1:]
        if not rest.empty:
            rest = rest.copy()
            rest["_year"] = rest["SnapshotFile"].apply(_extract_end_year)
            years = sorted(rest["_year"].unique(), reverse=True)

            for year in years:
                year_sub = rest[rest["_year"] == year]
                count = len(year_sub)

                # All history expanders collapsed by default
                with st.expander(f"📅 {year}  ·  {count} snapshots", expanded=False):
                    for _, r in year_sub.iterrows():
                        _render_one_snapshot(platform, r)

        st.markdown("---")
