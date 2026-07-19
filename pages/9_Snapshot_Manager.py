import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime

from app import (
    HISTORY_FILE,
    TRADES_HISTORY_FILE,
    SNAPSHOT_DIR,
    load_css,
    detect_coverage_gaps,
)

# Import per-platform snapshot dirs
from ibkr import IBKR_SNAPSHOT_DIR
from tiger import TIGER_SNAPSHOT_DIR
from moomoo import MOOMOO_SNAPSHOT_DIR


st.set_page_config(page_title="Snapshot Manager", page_icon="🗂️", layout="wide")

# ⭐ Hide "app" from sidebar navigation
st.markdown("""
<style>
[data-testid="stSidebarNav"] ul li:first-child { display: none; }
</style>
""", unsafe_allow_html=True)

load_css()

st.title("🗂️ Snapshot Manager")
st.caption("Manage uploaded statements. Review coverage, delete accidental uploads.")


# ============================================================
# CONSTANTS
# ============================================================

PLATFORMS = {
    "IBKR":   IBKR_SNAPSHOT_DIR,
    "Tiger":  TIGER_SNAPSHOT_DIR,
    "Moomoo": MOOMOO_SNAPSHOT_DIR,
}

DEPLOY_MARKER = os.path.join(SNAPSHOT_DIR, ".snapshot_manager_deployed")


def _get_deploy_cutoff():
    if not os.path.exists(DEPLOY_MARKER):
        try:
            with open(DEPLOY_MARKER, "w") as f:
                f.write(datetime.now().isoformat())
        except:
            pass
    try:
        return os.path.getmtime(DEPLOY_MARKER)
    except:
        return datetime.now().timestamp()


DEPLOY_CUTOFF = _get_deploy_cutoff()


# ============================================================
# HELPERS
# ============================================================

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


def _extract_date_range(snap_name):
    m = re.search(r"\((\d{8})-(\d{8})\)", str(snap_name))
    if not m:
        return "", ""
    s = m.group(1)
    e = m.group(2)
    return (
        f"{s[:4]}-{s[4:6]}-{s[6:8]}",
        f"{e[:4]}-{e[4:6]}-{e[6:8]}",
    )


def _extract_end_year(snap_name):
    _, e = _extract_date_range(snap_name)
    return e[:4] if e else "Unknown"


def _count_impact(snap_name, platform, history_df, trades_df):
    port_rows = 0
    trade_rows = 0

    if not history_df.empty and "SnapshotFile" in history_df.columns:
        port_rows = int(
            ((history_df["Platform"] == platform)
             & (history_df["SnapshotFile"] == snap_name)).sum()
        )

    if not trades_df.empty and "SnapshotFile" in trades_df.columns:
        trade_rows = int(
            ((trades_df["Platform"] == platform)
             & (trades_df["SnapshotFile"] == snap_name)).sum()
        )

    return port_rows, trade_rows


def _is_deletable(platform, snap_name):
    snap_dir = PLATFORMS.get(platform)
    if not snap_dir:
        return False
    snap_path = os.path.join(snap_dir, snap_name)
    if not os.path.exists(snap_path):
        return False
    try:
        return os.path.getmtime(snap_path) >= DEPLOY_CUTOFF
    except:
        return False


def _delete_snapshot(snap_name, platform):
    # 1. Remove from portfolio history
    if os.path.exists(HISTORY_FILE):
        try:
            hdf = pd.read_csv(HISTORY_FILE)
            if not hdf.empty and "SnapshotFile" in hdf.columns:
                mask = ~(
                    (hdf["Platform"] == platform)
                    & (hdf["SnapshotFile"] == snap_name)
                )
                hdf[mask].to_csv(HISTORY_FILE, index=False)
        except:
            pass

    # 2. Remove from trades history
    if os.path.exists(TRADES_HISTORY_FILE):
        try:
            tdf = pd.read_csv(TRADES_HISTORY_FILE, dtype=str)
            if not tdf.empty and "SnapshotFile" in tdf.columns:
                mask = ~(
                    (tdf["Platform"] == platform)
                    & (tdf["SnapshotFile"] == snap_name)
                )
                tdf[mask].to_csv(TRADES_HISTORY_FILE, index=False)
        except:
            pass

    # 3. Delete physical file
    snap_dir = PLATFORMS.get(platform)
    if snap_dir:
        snap_path = os.path.join(snap_dir, snap_name)
        if os.path.exists(snap_path):
            try:
                os.remove(snap_path)
            except:
                pass

    # ⭐ Clear ALL Streamlit caches so detect_coverage_gaps recomputes fresh
    try:
        st.cache_data.clear()
    except:
        pass


# ============================================================
# LOAD DATA (fresh every run — no caching)
# ============================================================

history_df = _load_history()
trades_df = _load_trades()


# ============================================================
# COVERAGE OVERVIEW (using your existing app.detect_coverage_gaps)
# ============================================================

st.markdown(
    "<div class='section-title'>📊 Coverage Overview</div>",
    unsafe_allow_html=True
)

for platform in PLATFORMS.keys():
    coverage_info = detect_coverage_gaps(platform)

    if not coverage_info.get("ranges"):
        continue

    n_statements = len(coverage_info["ranges"])
    covered = coverage_info["covered_days"]
    total = coverage_info["total_days"]
    n_gaps = len(coverage_info["gaps"])
    n_overlaps = len(coverage_info["overlaps"])

    if n_gaps == 0:
        gap_color = "#66FF99"
        gap_icon = "✅"
        gap_msg = "无日期缺口"
    else:
        gap_color = "#FFC300"
        gap_icon = "⚠️"
        gap_msg = f"{n_gaps} 个缺口"

    overlap_color = "#FFC300" if n_overlaps > 0 else "white"

    gap_rows_html = ""
    if coverage_info["gaps"]:
        for gs, ge in coverage_info["gaps"]:
            gap_rows_html += (
                f"<div style='color:#FFC300; font-size:13px; padding:4px 0;'>"
                f"• {gs} → {ge}"
                f"</div>"
            )

    overlap_rows_html = ""
    if coverage_info["overlaps"]:
        for ov in coverage_info["overlaps"]:
            try:
                overlap_rows_html += (
                    f"<div style='color:#FFC300; font-size:13px; padding:4px 0;'>"
                    f"• {ov[0]} ↔ {ov[1]}"
                    f"</div>"
                )
            except:
                overlap_rows_html += (
                    f"<div style='color:#FFC300; font-size:13px; padding:4px 0;'>"
                    f"• {ov}"
                    f"</div>"
                )

    detail_html = ""
    if coverage_info["gaps"] or coverage_info["overlaps"]:
        detail_html = (
            "<div style='border-top:1px solid #333; padding-top:12px; margin-top:14px;'>"
        )
        if coverage_info["gaps"]:
            detail_html += (
                "<div style='color:gray; font-size:12px; margin-bottom:6px;'>"
                "⚠️ Missing date ranges:</div>" + gap_rows_html
            )
        if coverage_info["overlaps"]:
            detail_html += (
                "<div style='color:gray; font-size:12px; margin-top:10px; margin-bottom:6px;'>"
                "⚠️ Overlapping ranges:</div>" + overlap_rows_html
            )
        detail_html += (
            "<div style='color:gray; font-size:12px; margin-top:8px;'>"
            "👉 建议补一份 statement 覆盖缺口，否则 FIFO 可能不准。</div>"
            "</div>"
        )

    st.markdown(f"""
    <div class='card' style='padding:20px; border-left:4px solid {gap_color}; margin-bottom:16px;'>

    <div style='color:gray; font-size:13px; margin-bottom:12px;'>
    <b style='color:white; font-size:15px;'>{platform}</b>
    </div>

    <div style='display:grid;
                grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
                gap:20px;'>

    <div>
    <div style='color:gray; font-size:13px;'>Statements</div>
    <div style='color:white; font-size:22px; font-weight:bold;'>{n_statements}</div>
    </div>

    <div>
    <div style='color:gray; font-size:13px;'>Coverage</div>
    <div style='color:white; font-size:22px; font-weight:bold;'>
    {covered} / {total} 天
    </div>
    </div>

    <div>
    <div style='color:gray; font-size:13px;'>Gaps</div>
    <div style='color:{gap_color}; font-size:22px; font-weight:bold;'>
    {gap_icon} {gap_msg}
    </div>
    </div>

    <div>
    <div style='color:gray; font-size:13px;'>Overlaps</div>
    <div style='color:{overlap_color}; font-size:22px; font-weight:bold;'>{n_overlaps}</div>
    </div>

    </div>

    {detail_html}

    </div>
    """, unsafe_allow_html=True)


# ============================================================
# DELETE DIALOG
# ============================================================

@st.dialog("Confirm Delete")
def _delete_dialog(platform, snap_name):
    port_rows, trade_rows = _count_impact(
        snap_name, platform, history_df, trades_df
    )

    st.markdown(f"Delete `{snap_name}` from **{platform}**?")
    st.markdown("**This will remove:**")
    st.markdown(f"- {port_rows} portfolio_history row(s)")
    st.markdown(f"- {trade_rows} trades_history row(s)")
    st.markdown("- The physical snapshot file")

    st.warning("⚠️ This action cannot be undone.")

    c1, c2 = st.columns(2)

    if c1.button("✅ Confirm Delete", type="primary", use_container_width=True):
        _delete_snapshot(snap_name, platform)
        st.rerun()

    if c2.button("❌ Cancel", use_container_width=True):
        st.rerun()


# ============================================================
# PER-PLATFORM TABLES
# ============================================================

st.markdown(
    "<div class='section-title'>📁 Snapshots</div>",
    unsafe_allow_html=True
)

current_year = datetime.now().year

for platform in PLATFORMS.keys():
    st.markdown(f"#### {platform}")

    if history_df.empty or "SnapshotFile" not in history_df.columns:
        st.caption("No snapshots yet.")
        continue

    sub = history_df[history_df["Platform"] == platform].copy()
    if sub.empty:
        st.caption("No snapshots yet.")
        continue

    sub = sub.drop_duplicates(subset=["SnapshotFile"], keep="last")

    def _sort_key(name):
        _, e = _extract_date_range(name)
        return e or ""
    sub["_sort_key"] = sub["SnapshotFile"].apply(_sort_key)
    sub = sub.sort_values("_sort_key", ascending=False)

    sub["_year"] = sub["SnapshotFile"].apply(_extract_end_year)
    years = sorted(sub["_year"].unique(), reverse=True)

    for year in years:
        year_sub = sub[sub["_year"] == year]
        count = len(year_sub)
        is_current = str(year) == str(current_year)
        label = f"📅 {year} ({count} snapshot{'s' if count != 1 else ''})"

        with st.expander(label, expanded=is_current):
            header_cols = st.columns([4, 1.3, 1.3, 1, 1, 1])
            header_cols[0].markdown("**Snapshot File**")
            header_cols[1].markdown("**Start**")
            header_cols[2].markdown("**End**")
            header_cols[3].markdown("**Port**")
            header_cols[4].markdown("**Trades**")
            header_cols[5].markdown("**Action**")

            for _, r in year_sub.iterrows():
                snap = str(r["SnapshotFile"])
                start, end = _extract_date_range(snap)
                port_rows, trade_rows = _count_impact(
                    snap, platform, history_df, trades_df
                )
                deletable = _is_deletable(platform, snap)

                cols = st.columns([4, 1.3, 1.3, 1, 1, 1])
                cols[0].write(snap)
                cols[1].write(start or "—")
                cols[2].write(end or "—")
                cols[3].write(port_rows)
                cols[4].write(trade_rows)

                if deletable:
                    if cols[5].button(
                        "🗑",
                        key=f"del_{platform}_{snap}",
                        help="Delete this snapshot",
                    ):
                        _delete_dialog(platform, snap)
                else:
                    cols[5].markdown(
                        "🔒",
                        help="Legacy snapshot (uploaded before Snapshot Manager) — read-only"
                    )

    st.markdown("---")
