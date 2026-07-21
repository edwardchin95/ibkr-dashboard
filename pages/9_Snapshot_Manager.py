import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime

from app import (
    require_auth,
    load_css,
    HISTORY_FILE,
    TRADES_HISTORY_FILE,
    SNAPSHOT_DIR,
    detect_coverage_gaps,
)

from ibkr import IBKR_SNAPSHOT_DIR
from tiger import TIGER_SNAPSHOT_DIR
from moomoo import MOOMOO_SNAPSHOT_DIR


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
    "IBKR": IBKR_SNAPSHOT_DIR,
    "Tiger": TIGER_SNAPSHOT_DIR,
    "Moomoo": MOOMOO_SNAPSHOT_DIR,
}

DEPLOY_MARKER = os.path.join(SNAPSHOT_DIR, ".snapshot_manager_deployed")


# ============================================================
# HELPERS
# ============================================================

def _get_deploy_cutoff():
    if not os.path.exists(DEPLOY_MARKER):
        try:
            os.makedirs(os.path.dirname(DEPLOY_MARKER), exist_ok=True)
            with open(DEPLOY_MARKER, "w") as f:
                f.write(datetime.now().isoformat())
        except:
            pass
    try:
        return os.path.getmtime(DEPLOY_MARKER)
    except:
        return datetime.now().timestamp()


DEPLOY_CUTOFF = _get_deploy_cutoff()


def _load_history():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    try:
        return pd.read_csv(HISTORY_FILE)
    except:
        return pd.DataFrame()


def _load_trades():
    if not os.path.exists(TRADES_HISTORY_FILE):
        return pd.DataFrame()
    try:
        return pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
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


def _is_deletable(platform, snapshot_name):
    snap_dir = PLATFORMS.get(platform)
    if not snap_dir:
        return False
    snap_path = os.path.join(snap_dir, snapshot_name)
    if not os.path.exists(snap_path):
        return False
    try:
        return os.path.getmtime(snap_path) >= DEPLOY_CUTOFF
    except:
        return False


def _delete_snapshot(platform, snapshot_name):
    if os.path.exists(HISTORY_FILE):
        try:
            hdf = pd.read_csv(HISTORY_FILE)
            if not hdf.empty and "Platform" in hdf.columns and "SnapshotFile" in hdf.columns:
                mask = ~(
                    (hdf["Platform"].astype(str) == platform)
                    & (hdf["SnapshotFile"].astype(str) == snapshot_name)
                )
                hdf = hdf[mask]
                hdf.to_csv(HISTORY_FILE, index=False)
        except:
            pass

    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            tdf = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
            if not tdf.empty and "Platform" in tdf.columns and "SnapshotFile" in tdf.columns:
                mask = ~(
                    (tdf["Platform"].astype(str) == platform)
                    & (tdf["SnapshotFile"].astype(str) == snapshot_name)
                )
                tdf = tdf[mask]
                tdf.to_csv(TRADES_HISTORY_FILE, index=False)
        except:
            pass

    snap_dir = PLATFORMS.get(platform)
    if snap_dir:
        snap_path = os.path.join(snap_dir, snapshot_name)
        if os.path.exists(snap_path):
            try:
                os.remove(snap_path)
            except:
                pass

    try:
        st.cache_data.clear()
    except:
        pass


# ============================================================
# COVERAGE RENDERER — uses native Streamlit metric (no HTML)
# ============================================================

def _render_coverage_card(platform):
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
    _render_coverage_card(platform)

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

    if c1.button("✅ Yes, Delete", type="primary", use_container_width=True):
        _delete_snapshot(platform, snapshot_name)
        st.session_state.pop("_pending_delete_snapshot", None)
        st.success("✅ Snapshot deleted.")
        st.rerun()

    if c2.button("❌ Cancel", use_container_width=True):
        st.session_state.pop("_pending_delete_snapshot", None)
        st.rerun()


if st.session_state.get("_pending_delete_snapshot"):
    p, s = st.session_state["_pending_delete_snapshot"]
    _confirm_delete_dialog(p, s)


# ============================================================
# SNAPSHOT CARDS
# ============================================================

st.markdown("## 📁 Snapshots")

if history_df.empty or "SnapshotFile" not in history_df.columns or "Platform" not in history_df.columns:
    st.info("No snapshot history found.")
else:
    current_year = datetime.now().year

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

        sub["_year"] = sub["SnapshotFile"].apply(_extract_end_year)
        years = sorted(sub["_year"].unique(), reverse=True)

        for year in years:
            year_sub = sub[sub["_year"] == year].copy()
            count = len(year_sub)
            expanded = str(year) == str(current_year)

            with st.expander(f"📅 {year}  ·  {count} snapshots", expanded=expanded):
                for _, r in year_sub.iterrows():
                    snapshot_name = _clean_str(r.get("SnapshotFile", ""))
                    if not snapshot_name:
                        continue

                    start, end = _extract_date_range(snapshot_name)
                    start_pretty = _pretty_date(start)
                    end_pretty = _pretty_date(end)

                    portfolio_rows, trade_rows = _count_impact(
                        snapshot_name, platform, history_df, trades_df
                    )
                    deletable = _is_deletable(platform, snapshot_name)

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
                            use_container_width=True,
                            type="secondary",
                        ):
                            st.session_state["_pending_delete_snapshot"] = (
                                platform,
                                snapshot_name,
                            )
                            st.rerun()

                    st.markdown("")

        st.markdown("---")