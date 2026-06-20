import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os

st.set_page_config(page_title="Overview", page_icon="📊", layout="wide")

from app import require_auth, load_css, detect_platform, HISTORY_FILE, format_df

# IBKR
from ibkr import (
    load_latest_snapshot as ibkr_load_latest_snapshot,
    process_incoming as ibkr_process_incoming,
    analyze_positions,
    extract_nav_cash as ibkr_extract_nav_cash,
    parse_ibkr_csv,
    extract_total_pnl as ibkr_extract_total_pnl,
    extract_total_deposit as ibkr_extract_total_deposit,
    save_snapshot_and_history as ibkr_save_snapshot_and_history,
    load_realized_pnl_summary as ibkr_load_realized_pnl_summary,
    TARGET_ETF_STOCK_TOTAL, TARGET_SINGLE_STOCK,
    TARGET_OPTION_TOTAL, TARGET_CASH,
    OPTION_TARGETS, OPTION_COLORS,
)

# Tiger
from tiger import (
    load_latest_snapshot as tiger_load_latest_snapshot,
    process_incoming as tiger_process_incoming,
    save_snapshot_and_history as tiger_save_snapshot_and_history,
    load_realized_pnl_summary_sgd as tiger_load_realized_pnl_summary_sgd,
)

# ============================================================
# 验证 + CSS
# ============================================================
require_auth()
load_css()

# ============================================================
# AUTO-PROCESS INCOMING
# ============================================================
ibkr_process_incoming()
tiger_process_incoming()

# ============================================================
# SIDEBAR
# ============================================================
st.sidebar.title("📁 数据上传")

uploaded_file = st.sidebar.file_uploader(
    "上传 CSV（自动识别平台）",
    type="csv",
    key="overview_upload"
)

# 🔥 Platform Filter
platform_filter = st.sidebar.selectbox(
    "🔍 平台筛选",
    ["All", "IBKR", "Tiger"],
    index=0,
    key="platform_filter"
)

target_nav = st.sidebar.number_input(
    "🎯 目标净值 SGD",
    value=100000,
    step=10000
)

st.sidebar.info("✅ 自动识别平台\n• IBKR Flex Query CSV\n• Tiger Activity Statement CSV")

# ============================================================
# UPLOAD HANDLER
# ============================================================
if uploaded_file is not None:

    file_bytes = uploaded_file.getvalue()
    platform = detect_platform(file_bytes)

    if platform == "IBKR":
        uploaded_file.seek(0)
        ibkr_total_nav, ibkr_cash_v, _, _ = ibkr_extract_nav_cash(uploaded_file)

        uploaded_file.seek(0)
        ibkr_pnl_v = ibkr_extract_total_pnl(uploaded_file)

        uploaded_file.seek(0)
        ibkr_deposit_raw = ibkr_extract_total_deposit(uploaded_file)

        uploaded_file.seek(0)
        ibkr_save_snapshot_and_history(
            uploaded_file, ibkr_total_nav, ibkr_cash_v, ibkr_pnl_v, ibkr_deposit_raw
        )

        st.sidebar.success("✅ IBKR 数据已更新")
        st.cache_data.clear()

    elif platform == "Tiger":
        uploaded_file.seek(0)
        tiger_save_snapshot_and_history(uploaded_file)

        st.sidebar.success("✅ Tiger 数据已更新")
        st.cache_data.clear()

    else:
        st.sidebar.error("❌ 无法识别此文件属于哪个平台")

# ============================================================
# LOAD BOTH PLATFORMS
# ============================================================
ibkr_loaded = ibkr_load_latest_snapshot()
tiger_loaded = tiger_load_latest_snapshot()

# IBKR data
ibkr_nav = ibkr_cash = ibkr_pnl = ibkr_deposit = 0
ibkr_positions = pd.DataFrame()
ibkr_history = pd.DataFrame()

if ibkr_loaded is not None:
    ibkr_positions = ibkr_loaded["df_positions"]
    ibkr_history = ibkr_loaded["history_df"]
    ibkr_nav = float(ibkr_loaded["nav"])
    ibkr_cash = float(ibkr_loaded["cash"])
    ibkr_pnl = float(ibkr_loaded["pnl"])
    ibkr_deposit = float(ibkr_loaded["deposit"])

# Tiger data
tiger_nav = tiger_cash_v = tiger_pnl = tiger_deposit = 0
tiger_positions = pd.DataFrame()
tiger_history = pd.DataFrame()

if tiger_loaded is not None:
    tiger_positions = tiger_loaded["df_positions"]
    tiger_history = tiger_loaded["history_df"]
    tiger_nav = float(tiger_loaded["nav"])
    tiger_cash_v = float(tiger_loaded["cash"])
    tiger_pnl = float(tiger_loaded["pnl"])
    tiger_deposit = float(tiger_loaded["deposit"])

# ============================================================
# APPLY PLATFORM FILTER
# ============================================================
if platform_filter == "IBKR":
    df_positions = ibkr_positions
    history_df = ibkr_history
    total_nav = ibkr_nav
    cash_sgd = ibkr_cash
    real_pnl = ibkr_pnl
    total_deposit = ibkr_deposit
elif platform_filter == "Tiger":
    df_positions = tiger_positions
    history_df = tiger_history
    total_nav = tiger_nav
    cash_sgd = tiger_cash_v
    real_pnl = tiger_pnl
    total_deposit = tiger_deposit
else:  # All
    if not ibkr_positions.empty or not tiger_positions.empty:
        df_positions = pd.concat([ibkr_positions, tiger_positions], ignore_index=True)
    else:
        df_positions = pd.DataFrame()

    if not ibkr_history.empty and not tiger_history.empty:
        history_df = pd.concat([ibkr_history, tiger_history], ignore_index=True)
    elif not ibkr_history.empty:
        history_df = ibkr_history
    elif not tiger_history.empty:
        history_df = tiger_history
    else:
        history_df = pd.DataFrame()

    total_nav = ibkr_nav + tiger_nav
    cash_sgd = ibkr_cash + tiger_cash_v
    real_pnl = ibkr_pnl + tiger_pnl
    total_deposit = ibkr_deposit + tiger_deposit

# ============================================================
# PREVIOUS NAV (跟 filter 走)
# ============================================================
previous_nav = total_nav
nav_change = 0
nav_pct = 0

if platform_filter == "IBKR" and not ibkr_history.empty and len(ibkr_history) > 1:
    previous_nav = float(ibkr_history.iloc[-2]["NAV"])
    nav_change = total_nav - previous_nav
    nav_pct = (nav_change / previous_nav * 100) if previous_nav != 0 else 0
elif platform_filter == "Tiger" and not tiger_history.empty and len(tiger_history) > 1:
    previous_nav = float(tiger_history.iloc[-2]["NAV"])
    nav_change = total_nav - previous_nav
    nav_pct = (nav_change / previous_nav * 100) if previous_nav != 0 else 0
elif platform_filter == "All":
    prev_ibkr = float(ibkr_history.iloc[-2]["NAV"]) if not ibkr_history.empty and len(ibkr_history) > 1 else ibkr_nav
    prev_tiger = float(tiger_history.iloc[-2]["NAV"]) if not tiger_history.empty and len(tiger_history) > 1 else tiger_nav
    previous_nav = prev_ibkr + prev_tiger
    nav_change = total_nav - previous_nav
    nav_pct = (nav_change / previous_nav * 100) if previous_nav != 0 else 0

# ============================================================
# PORTFOLIO RETURN
# ============================================================
portfolio_return = total_nav - total_deposit

# ============================================================
# REALIZED PROFIT / LOSS (按 filter)
# ============================================================
ibkr_realized = ibkr_load_realized_pnl_summary()
tiger_realized = tiger_load_realized_pnl_summary_sgd()

if platform_filter == "IBKR":
    total_realized_profit = ibkr_realized["realized_profit"]
    total_realized_loss = ibkr_realized["realized_loss"]
elif platform_filter == "Tiger":
    total_realized_profit = tiger_realized["realized_profit"]
    total_realized_loss = tiger_realized["realized_loss"]
else:
    total_realized_profit = ibkr_realized["realized_profit"] + tiger_realized["realized_profit"]
    total_realized_loss = ibkr_realized["realized_loss"] + tiger_realized["realized_loss"]

# ============================================================
# OVERVIEW CARD
# ============================================================
st.title("📊 Portfolio Overview")

filter_label = f" — {platform_filter}" if platform_filter != "All" else ""
st.subheader(f"账户总览 PORTFOLIO OVERVIEW{filter_label}")

portfolio_color = "#66FF99" if portfolio_return >= 0 else "#FF6666"
change_color = "#66FF99" if nav_change >= 0 else "#FF6666"
pnl_color = "#66FF99" if real_pnl >= 0 else "#FF6666"

progress = 0
if target_nav != 0:
    progress = total_nav / target_nav * 100

remaining = target_nav - total_nav

st.markdown(f"""
<div class='card' style='padding:24px;'>

<div style='display:grid;
            grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
            gap:20px;
            margin-bottom:20px;'>

<div>
<div style='color:gray; font-size:13px;'>Total NAV</div>
<div style='color:white; font-size:24px; font-weight:bold;'>
SGD ${total_nav:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Holding P&L</div>
<div style='color:{pnl_color}; font-size:24px; font-weight:bold;'>
SGD ${real_pnl:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Total Deposit</div>
<div style='color:white; font-size:24px; font-weight:bold;'>
SGD ${total_deposit:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>本次变化</div>
<div style='color:{change_color}; font-size:24px; font-weight:bold;'>
SGD ${nav_change:,.2f} ({nav_pct:.2f}%)
</div>
</div>

</div>

<div style='border-top:1px solid #333;
            padding-top:16px;
            display:grid;
            grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
            gap:20px;
            margin-bottom:16px;'>

<div>
<div style='color:gray; font-size:13px;'>Portfolio Return</div>
<div style='color:{portfolio_color}; font-size:22px; font-weight:bold;'>
SGD ${portfolio_return:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>上次净值</div>
<div style='color:white; font-size:22px; font-weight:bold;'>
SGD ${previous_nav:,.2f}
</div>
</div>

</div>

<div style='border-top:1px solid #333;
            padding-top:16px;
            display:grid;
            grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
            gap:20px;
            margin-bottom:20px;'>

<div>
<div style='color:gray; font-size:13px;'>Cumulative Realized Profit</div>
<div style='color:#66FF99; font-size:22px; font-weight:bold;'>
SGD ${total_realized_profit:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Cumulative Realized Loss</div>
<div style='color:#FF6666; font-size:22px; font-weight:bold;'>
SGD ${total_realized_loss:,.2f}
</div>
</div>

</div>

<div style='border-top:1px solid #333; padding-top:14px;'>

<div class='progress-container' style='margin-top:10px;'>
<div class='progress-bar'
style='width:{progress:.1f}%;
background: linear-gradient(90deg, #00D4FF, #4A7BFF);'>
</div>
</div>

<div style='display:flex; justify-content:space-between; margin-top:8px; flex-wrap:wrap; gap:8px;'>
<span style='color:white; font-size:13px;'>{progress:.1f}% 完成 / 目标 SGD ${target_nav:,.2f}</span>
<span style='color:gray; font-size:13px;'>距离目标 SGD ${remaining:,.2f}</span>
</div>

</div>

</div>
""", unsafe_allow_html=True)

# ============================================================
# ANALYZE POSITIONS
# ============================================================
analysis = analyze_positions(df_positions, total_nav, cash_sgd)

index_etf_positions = analysis["index_etf_positions"]
stock_positions = analysis["stock_positions"]
option_categories = analysis["option_categories"]
option_positions = analysis["option_positions"]
option_total_exposure = analysis["option_total_exposure"]
fx_ratio = analysis["fx_ratio"]
stock_pct_signed = analysis["stock_pct_signed"]
option_pct_signed = analysis["option_pct_signed"]
option_pct_exposure = analysis["option_pct_exposure"]
cash_pct = analysis["cash_pct"]
stock_nav_sgd_local = analysis["stock_nav_sgd"]
option_nav_sgd_local = analysis["option_nav_sgd"]

# Merge ETF/Stock 同 symbol 跨平台
def _merge_by_symbol(positions_list):
    merged = {}
    for p in positions_list:
        sym = p["Symbol"]
        if sym in merged:
            merged[sym]["Value"] += p["Value"]
        else:
            merged[sym] = {"Symbol": sym, "Value": p["Value"]}
    return list(merged.values())

if platform_filter == "All":
    index_etf_positions = _merge_by_symbol(index_etf_positions)
    stock_positions = _merge_by_symbol(stock_positions)

# ============================================================
# 🏦 平台占比
# ============================================================
st.markdown(
    "<div class='section-title'>🏦 平台占比</div>",
    unsafe_allow_html=True
)

if platform_filter == "All":
    platform_data = []
    if ibkr_nav > 0:
        platform_data.append({"Platform": "IBKR", "NAV": ibkr_nav})
    if tiger_nav > 0:
        platform_data.append({"Platform": "Tiger", "NAV": tiger_nav})
elif platform_filter == "IBKR":
    platform_data = [{"Platform": "IBKR", "NAV": ibkr_nav}] if ibkr_nav > 0 else []
else:
    platform_data = [{"Platform": "Tiger", "NAV": tiger_nav}] if tiger_nav > 0 else []

if platform_data:
    platform_df = pd.DataFrame(platform_data)

    col1, col2 = st.columns([1, 1])

    with col1:

        fig_platform = go.Figure(
            data=[
                go.Pie(
                    labels=platform_df["Platform"],
                    values=platform_df["NAV"],
                    hole=0.65,
                    textinfo="label+percent",
                    textfont=dict(color="white", size=14),
                    showlegend=False
                )
            ]
        )

        fig_platform.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="white"
        )

        st.plotly_chart(fig_platform, use_container_width=True)

    with col2:

        st.markdown("### 💰 Platform Holdings")

        for _, row in platform_df.iterrows():

            pct = (
                row["NAV"] / total_nav * 100
            ) if total_nav != 0 else 0

            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                border-bottom:1px solid #E5E7EB;
                flex-wrap:wrap;
                gap:8px;'>

                <span style='font-weight:bold; color:black;'>
                {row['Platform']}
                </span>

                <span style='color:#666;'>
                SGD ${row['NAV']:,.2f} ({pct:.1f}%)
                </span>

                </div>
                """,
                unsafe_allow_html=True
            )

        if len(platform_df) > 1:
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                border-top:2px solid #333;
                flex-wrap:wrap;
                gap:8px;'>
                <span style='font-weight:bold; color:black; font-size:18px;'>Total</span>
                <span style='font-weight:bold; color:black; font-size:18px;'>SGD ${total_nav:,.2f}</span>
                </div>
                """,
                unsafe_allow_html=True
            )

# ============================================================
# CHART 1 - 总资产配置（% NetLiq）+ Targets
# ============================================================
st.markdown(
    "<div class='section-title'>📊 持仓分析</div>",
    unsafe_allow_html=True
)

st.markdown("### 1️⃣ 总资产配置（% NetLiq）")

col1, col2 = st.columns([1, 1])

with col1:

    bar_labels = ["ETF + Stocks", "Options", "Cash"]

    bar_values = [stock_pct_signed, option_pct_signed, cash_pct]

    bar_colors = [
        "#4A7BFF",
        "#FF6666" if option_pct_signed < 0 else "#FFB800",
        "#00D4AA"
    ]

    fig1 = go.Figure()

    fig1.add_trace(
        go.Bar(
            x=bar_values,
            y=bar_labels,
            orientation="h",
            marker_color=bar_colors,
            text=[f"{v:.1f}%" for v in bar_values],
            textposition="outside"
        )
    )

    fig1.add_vline(x=0, line_width=1, line_color="white")

    fig1.update_layout(
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        font_color="white",
        xaxis_title="% NetLiq",
        yaxis_title="",
        showlegend=False,
        height=350
    )

    st.plotly_chart(fig1, use_container_width=True)

with col2:

    st.markdown("### 🎯 Allocation Targets")

    allocation_data = [
        {
            "Name": "ETF + Stocks",
            "CurrentDisplay": stock_pct_signed,
            "CurrentCheck": abs(stock_pct_signed),
            "Target": TARGET_ETF_STOCK_TOTAL,
            "Color": "#4A7BFF",
            "Amount": stock_nav_sgd_local
        },
        {
            "Name": "Options",
            "CurrentDisplay": option_pct_signed,
            "CurrentCheck": option_pct_exposure,
            "Target": TARGET_OPTION_TOTAL,
            "Color": "#FFB800",
            "Amount": option_nav_sgd_local
        },
        {
            "Name": "Cash",
            "CurrentDisplay": cash_pct,
            "CurrentCheck": cash_pct,
            "Target": TARGET_CASH,
            "Color": "#00D4AA",
            "Amount": cash_sgd
        }
    ]

    for item in allocation_data:

        current_display = item["CurrentDisplay"]
        current_check = item["CurrentCheck"]
        target = item["Target"]
        amount = item["Amount"]

        status = "✅"
        if abs(current_check) > target:
            status = "⚠️"

        st.markdown(
            f"""
            <div style='margin-bottom:26px;'>

            <div style='display:flex;
            justify-content:space-between;
            color:black;
            font-weight:bold;
            margin-bottom:6px;
            flex-wrap:wrap;
            gap:8px;'>

            <span>
            {status} {item['Name']}
            </span>

            <span>
            {current_display:.1f}% ({target}%)
            </span>

            </div>

            <div style='color:#666666;
            margin-bottom:8px;'>

            SGD ${amount:,.2f}

            </div>

            <div class='progress-container'>

            <div class='progress-bar'
            style='width:{min(abs(current_check),100):.1f}%;
            background:{item['Color']};'>
            </div>

            </div>

            </div>
            """,
            unsafe_allow_html=True
        )

# ============================================================
# CHART 2 - ETF 分布 + Targets + 金额
# ============================================================

if len(index_etf_positions) > 0:

    st.markdown("### 2️⃣ 大盘 ETF 分布")

    col1, col2 = st.columns([1, 1])

    with col1:

        etf_labels = [p["Symbol"] for p in index_etf_positions]
        etf_values = [p["Value"] for p in index_etf_positions]

        fig2 = go.Figure(
            data=[
                go.Pie(
                    labels=etf_labels,
                    values=etf_values,
                    hole=0.65,
                    textinfo="label+percent",
                    textfont=dict(color="white", size=14),
                    showlegend=False
                )
            ]
        )

        fig2.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="white"
        )

        st.plotly_chart(fig2, use_container_width=True)

    with col2:

            st.markdown("### 💰 ETF Holdings")

            for p in index_etf_positions:
                value_sgd = p["Value"]
                pct = (
                    value_sgd / total_nav * 100
                ) if total_nav != 0 else 0

                st.markdown(
                    f"""
                    <div style='display:flex;
                    justify-content:space-between;
                    padding:12px 0;
                    border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap;
                    gap:8px;'>

                    <span style='font-weight:bold; color:black;'>
                    {p['Symbol']}
                    </span>

                    <span style='color:#666;'>
                    SGD ${value_sgd:,.2f} ({pct:.1f}%)
                    </span>

                    </div>
                    """,
                    unsafe_allow_html=True
                )

            etf_total_sgd = sum(p["Value"] for p in index_etf_positions)
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                border-top:2px solid #333;
                flex-wrap:wrap;
                gap:8px;'>

                <span style='font-weight:bold; color:black; font-size:18px;'>
                Total
                </span>

                <span style='font-weight:bold; color:black; font-size:18px;'>
                SGD ${etf_total_sgd:,.2f}
                </span>

                </div>
                """,
                unsafe_allow_html=True
            )

# ============================================================
# CHART 3 - 个股分布 + Targets + 金额
# ============================================================

if len(stock_positions) > 0:

    st.markdown("### 3️⃣ 个股分布")

    col1, col2 = st.columns([1, 1])

    with col1:

        stock_labels = [p["Symbol"] for p in stock_positions]
        stock_values = [p["Value"] for p in stock_positions]

        fig3 = go.Figure(
            data=[
                go.Pie(
                    labels=stock_labels,
                    values=stock_values,
                    hole=0.65,
                    textinfo="label+percent",
                    textfont=dict(color="white", size=14),
                    showlegend=False
                )
            ]
        )

        fig3.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="white"
        )

        st.plotly_chart(fig3, use_container_width=True)

    with col2:

        st.markdown("### 🎯 Individual Stock Targets")

        for p in stock_positions:

            value_sgd = p["Value"]

            pct = (
                value_sgd / total_nav * 100
            ) if total_nav != 0 else 0

            target = TARGET_SINGLE_STOCK
            warning = "✅"

            if pct > target:
                warning = "⚠️"

            st.markdown(
                f"""
                <div style='margin-bottom:26px;'>

                <div style='display:flex;
                justify-content:space-between;
                color:black;
                font-weight:bold;
                margin-bottom:6px;
                flex-wrap:wrap;
                gap:8px;'>

                <span>
                {warning} {p['Symbol']}
                </span>

                <span>
                {pct:.1f}% ({target}%)
                </span>

                </div>

                <div style='color:#666666;
                margin-bottom:8px;'>

                SGD ${value_sgd:,.2f}

                </div>

                <div class='progress-container'>

                <div class='progress-bar'
                style='width:{min(pct,100):.1f}%;
                background:#00D4AA;'>
                </div>

                </div>

                </div>
                """,
                unsafe_allow_html=True
            )

        stock_total_sgd = sum(p["Value"] for p in stock_positions)
        st.markdown(
            f"""
            <div style='display:flex;
            justify-content:space-between;
            padding:12px 0;
            border-top:2px solid #333;
            flex-wrap:wrap;
            gap:8px;'>

            <span style='font-weight:bold; color:black; font-size:18px;'>
            Total
            </span>

            <span style='font-weight:bold; color:black; font-size:18px;'>
            SGD ${stock_total_sgd:,.2f}
            </span>

            </div>
            """,
            unsafe_allow_html=True
        )

# ============================================================
# CHART 4 - 期权分布 + Targets + 金额
# ============================================================

if len(option_positions) > 0:

    st.markdown("### 4️⃣ 期权持仓分布（Exposure）")

    col1, col2 = st.columns([1, 1])

    with col1:

        option_labels = [
            k for k, v in option_categories.items()
            if v > 0
        ]

        option_values = [
            v for k, v in option_categories.items()
            if v > 0
        ]

        option_colors = [
            OPTION_COLORS.get(k, "#9CA3AF")
            for k, v in option_categories.items()
            if v > 0
        ]

        fig4 = go.Figure(
            data=[
                go.Pie(
                    labels=option_labels,
                    values=option_values,
                    hole=0.65,
                    marker_colors=option_colors,
                    textinfo="label+percent",
                    textfont=dict(color="white", size=14),
                    showlegend=False
                )
            ]
        )

        fig4.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="white"
        )

        st.plotly_chart(fig4, use_container_width=True)

    with col2:

            st.markdown("### 💰 Options Holdings")

            for category, exposure in option_categories.items():

                if exposure <= 0:
                    continue

                exposure_sgd = exposure

                pct = (
                    exposure_sgd / option_total_exposure * 100
                ) if option_total_exposure != 0 else 0

                color = OPTION_COLORS.get(category, "#9CA3AF")

                st.markdown(
                    f"""
                    <div style='display:flex;
                    justify-content:space-between;
                    padding:12px 0;
                    border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap;
                    gap:8px;'>

                    <span style='font-weight:bold; color:black;'>
                    <span style='color:{color};'>●</span> {category}
                    </span>

                    <span style='color:#666;'>
                    SGD ${exposure_sgd:,.2f} ({pct:.1f}%)
                    </span>

                    </div>
                    """,
                    unsafe_allow_html=True
                )

            option_total_sgd = sum(
                v for v in option_categories.values() if v > 0
            )
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                border-top:2px solid #333;
                flex-wrap:wrap;
                gap:8px;'>

                <span style='font-weight:bold; color:black; font-size:18px;'>
                Total
                </span>

                <span style='font-weight:bold; color:black; font-size:18px;'>
                SGD ${option_total_sgd:,.2f}
                </span>

                </div>
                """,
                unsafe_allow_html=True
            )
# ============================================================
# CASH（全平台现金汇总）
# ============================================================
st.markdown(
    "<div class='section-title'>💵 现金仓</div>",
    unsafe_allow_html=True
)

# Cash data 按 filter 走
if platform_filter == "All":
    cash_data = []
    if ibkr_nav > 0:
        cash_data.append({"Platform": "IBKR", "Cash": ibkr_cash})
    if tiger_nav > 0:
        cash_data.append({"Platform": "Tiger", "Cash": tiger_cash_v})
elif platform_filter == "IBKR":
    cash_data = [{"Platform": "IBKR", "Cash": ibkr_cash}] if ibkr_nav > 0 else []
else:
    cash_data = [{"Platform": "Tiger", "Cash": tiger_cash_v}] if tiger_nav > 0 else []

total_cash = sum(c["Cash"] for c in cash_data)
total_cash_pct = (total_cash / total_nav * 100) if total_nav != 0 else 0

col1, col2 = st.columns([1, 1])

with col1:

    fig_cash = go.Figure(
        data=[
            go.Pie(
                labels=["Cash", "Invested"],
                values=[total_cash, max(0, total_nav - total_cash)],
                hole=0.7,
                marker_colors=["#00FF88", "#1A1F2E"],
                textinfo="label+percent",
                textfont=dict(color="white", size=14)
            )
        ]
    )

    fig_cash.update_layout(
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        font_color="white"
    )

    st.plotly_chart(fig_cash, use_container_width=True)

with col2:

    st.markdown("### 💵 Cash by Platform")

    for c in cash_data:

        pct = (
            c["Cash"] / total_nav * 100
        ) if total_nav != 0 else 0

        st.markdown(
            f"""
            <div style='display:flex;
            justify-content:space-between;
            padding:12px 0;
            border-bottom:1px solid #E5E7EB;
            flex-wrap:wrap;
            gap:8px;'>

            <span style='font-weight:bold; color:black;'>
            {c['Platform']}
            </span>

            <span style='color:#666;'>
            SGD ${c['Cash']:,.2f} ({pct:.1f}%)
            </span>

            </div>
            """,
            unsafe_allow_html=True
        )

    st.markdown(
        f"""
        <div style='display:flex;
        justify-content:space-between;
        padding:12px 0;
        border-top:2px solid #333;
        margin-bottom:20px;
        flex-wrap:wrap;
        gap:8px;'>

        <span style='font-weight:bold; color:black; font-size:18px;'>
        Total Cash
        </span>

        <span style='font-weight:bold; color:black; font-size:18px;'>
        SGD ${total_cash:,.2f} ({total_cash_pct:.1f}%)
        </span>

        </div>
        """,
        unsafe_allow_html=True
    )

    status = "✅"
    if total_cash_pct > TARGET_CASH:
        status = "⚠️"

    st.markdown(
        f"""
        <div style='margin-bottom:26px;'>

        <div style='display:flex;
        justify-content:space-between;
        color:black;
        font-weight:bold;
        margin-bottom:6px;
        flex-wrap:wrap;
        gap:8px;'>

        <span>
        {status} Cash Target
        </span>

        <span>
        {total_cash_pct:.1f}% ({TARGET_CASH}%)
        </span>

        </div>

        <div class='progress-container'>

        <div class='progress-bar'
        style='width:{min(total_cash_pct,100):.1f}%;
        background:#00D4AA;'>
        </div>

        </div>

        </div>
        """,
        unsafe_allow_html=True
    )

# ============================================================
# POSITION DETAILS（已经含 Platform 列）
# ============================================================
st.markdown(
    "<div class='section-title'>📋 持仓明细</div>",
    unsafe_allow_html=True
)

if df_positions is not None and not df_positions.empty:
    positions_display = format_df(
        df_positions,  # 或 combined_positions，看你 code 用哪个变量名
        cols_2dp=["Quantity", "Multiplier", "CostPrice", "ClosePrice",
                "PositionValue", "PositionValueSgd",
                "UnrealizedPnL", "UnrealizedPnLSgd"],
    )
    st.dataframe(positions_display, use_container_width=True, hide_index=True)
else:
    st.info("暂无持仓数据")

# ============================================================
# HISTORY
# ============================================================
st.markdown(
    "<div class='section-title'>📈 Portfolio History</div>",
    unsafe_allow_html=True
)

# 决定要画哪条线
show_ibkr_line = platform_filter in ("All", "IBKR") and not ibkr_history.empty
show_tiger_line = platform_filter in ("All", "Tiger") and not tiger_history.empty

if show_ibkr_line or show_tiger_line:

    fig_history = go.Figure()

    if show_ibkr_line and "Timestamp" in ibkr_history.columns and "NAV" in ibkr_history.columns:
        fig_history.add_trace(
            go.Scatter(
                x=ibkr_history["Timestamp"],
                y=ibkr_history["NAV"],
                mode="lines+markers",
                line=dict(color="#00D4FF", width=3),
                marker=dict(size=8),
                name="IBKR"
            )
        )

    if show_tiger_line and "Timestamp" in tiger_history.columns and "NAV" in tiger_history.columns:
        fig_history.add_trace(
            go.Scatter(
                x=tiger_history["Timestamp"],
                y=tiger_history["NAV"],
                mode="lines+markers",
                line=dict(color="#FFC300", width=3),
                marker=dict(size=8),
                name="Tiger"
            )
        )

    # All 模式下加 Combined 线
    if platform_filter == "All" and show_ibkr_line and show_tiger_line:
        # 按 Timestamp 合并求和
        try:
            combined_history_df = pd.concat([ibkr_history, tiger_history], ignore_index=True)
            combined_grouped = combined_history_df.groupby("Timestamp")["NAV"].sum().reset_index()
            combined_grouped = combined_grouped.sort_values("Timestamp")

            fig_history.add_trace(
                go.Scatter(
                    x=combined_grouped["Timestamp"],
                    y=combined_grouped["NAV"],
                    mode="lines+markers",
                    line=dict(color="#66FF99", width=3, dash="dot"),
                    marker=dict(size=8),
                    name="Combined"
                )
            )
        except:
            pass

    fig_history.update_layout(
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        font_color="white",
        title="Portfolio Equity Curve",
        xaxis_title="Time",
        yaxis_title="NAV (SGD)"
    )

    st.plotly_chart(fig_history, use_container_width=True)

    # 表格也按 filter 走
    if history_df is not None and not history_df.empty and "Timestamp" in history_df.columns:
        history_display = format_df(
            history_df.sort_values(by="Timestamp", ascending=False),
            cols_2dp=["NAV", "Cash", "PnL", "TotalDeposit", "PeriodDeposit",
                    "Dividends", "WithholdingTax", "NetDividends", "Fees"],
            cols_3dp=["UsdToSgd"],
        )
        st.dataframe(history_display, use_container_width=True, hide_index=True)

else:
    st.info("暂无历史记录")