import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
st.set_page_config(page_title="IBKR", page_icon="🟧", layout="wide")

from app import require_auth, load_css
from ibkr import (
    load_latest_snapshot, process_incoming, analyze_positions,
    load_trades_history,
    load_cash_summary_total,
    load_realized_pnl_summary,
    OPTION_COLORS
)

# ============================================================
# 验证 + CSS
# ============================================================
require_auth()
load_css()

# ============================================================
# AUTO-PROCESS INCOMING
# ============================================================
process_incoming()

# ============================================================
# LOAD DATA
# ============================================================
df_positions = None
total_nav = 0
cash_sgd = 0
real_pnl = 0
total_deposit = 0

loaded = load_latest_snapshot()

if loaded is not None:
    df_positions = loaded["df_positions"]
    total_nav = loaded["nav"]
    cash_sgd = loaded["cash"]
    real_pnl = loaded["pnl"]
    total_deposit = loaded["deposit"]

# ============================================================
# REALIZED PROFIT / LOSS（IBKR 累计）
# ============================================================
realized_summary = load_realized_pnl_summary()
realized_profit = realized_summary["realized_profit"]
realized_loss = realized_summary["realized_loss"]

# ============================================================
# PAGE TITLE
# ============================================================
st.title("🟧 IBKR Portfolio")

# ============================================================
# IBKR Summary Card（GRID 响应式）
# ============================================================
cash_pct = (cash_sgd / total_nav * 100) if total_nav != 0 else 0
portfolio_return = total_nav - total_deposit
return_pct = (portfolio_return / total_deposit * 100) if total_deposit != 0 else 0
return_color = "#66FF99" if portfolio_return >= 0 else "#FF6666"
pnl_color = "#66FF99" if real_pnl >= 0 else "#FF6666"

st.markdown(f"""
<div class='card' style='padding:24px;'>

<div style='display:grid;
            grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
            gap:20px;
            margin-bottom:20px;'>

<div>
<div style='color:gray; font-size:13px;'>NAV</div>
<div style='color:white; font-size:24px; font-weight:bold;'>
SGD ${total_nav:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Cash</div>
<div style='color:white; font-size:24px; font-weight:bold;'>
SGD ${cash_sgd:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Holding P&L</div>
<div style='color:{pnl_color}; font-size:24px; font-weight:bold;'>
SGD ${real_pnl:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Total Deposits</div>
<div style='color:white; font-size:24px; font-weight:bold;'>
SGD ${total_deposit:,.2f}
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
<div style='color:gray; font-size:13px;'>Cash % of NAV</div>
<div style='color:#00FF88; font-size:22px; font-weight:bold;'>
{cash_pct:.1f}%
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Portfolio Return</div>
<div style='color:{return_color}; font-size:22px; font-weight:bold;'>
SGD ${portfolio_return:,.2f} ({return_pct:+.2f}%)
</div>
</div>

</div>

<div style='border-top:1px solid #333;
            padding-top:16px;
            display:grid;
            grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
            gap:20px;'>

<div>
<div style='color:gray; font-size:13px;'>Cumulative Realized Profit</div>
<div style='color:#66FF99; font-size:22px; font-weight:bold;'>
USD ${realized_profit:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Cumulative Realized Loss</div>
<div style='color:#FF6666; font-size:22px; font-weight:bold;'>
USD ${realized_loss:,.2f}
</div>
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
fx_ratio = analysis["fx_ratio"]

if df_positions is not None:

    # ============================================================
    # CHART 2 - 大盘 ETF 分布
    # ============================================================

    if len(index_etf_positions) > 0:

        st.markdown(
            "<div class='section-title'>📊 大盘 ETF 分布</div>",
            unsafe_allow_html=True
        )

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

                value_sgd = p["Value"] * abs(fx_ratio)

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

            etf_total_sgd = sum(p["Value"] * abs(fx_ratio) for p in index_etf_positions)
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                margin-top:4px;
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
    # CHART 3 - 个股分布
    # ============================================================

    if len(stock_positions) > 0:

        st.markdown(
            "<div class='section-title'>📊 个股分布</div>",
            unsafe_allow_html=True
        )

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

            st.markdown("### 💰 Stock Holdings")

            for p in stock_positions:

                value_sgd = p["Value"] * abs(fx_ratio)

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

            stock_total_sgd = sum(p["Value"] * abs(fx_ratio) for p in stock_positions)
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                margin-top:4px;
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
    # CHART 4 - 期权分布（Exposure）
    # ============================================================

    if len(option_positions) > 0:

        st.markdown(
            "<div class='section-title'>📊 期权持仓分布（Exposure）</div>",
            unsafe_allow_html=True
        )

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

                exposure_sgd = exposure * abs(fx_ratio)

                st.markdown(
                    f"""
                    <div style='display:flex;
                    justify-content:space-between;
                    padding:12px 0;
                    border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap;
                    gap:8px;'>

                    <span style='font-weight:bold; color:black;'>
                    {category}
                    </span>

                    <span style='color:#666;'>
                    SGD ${exposure_sgd:,.2f}
                    </span>

                    </div>
                    """,
                    unsafe_allow_html=True
                )

            option_total_sgd = sum(
                v * abs(fx_ratio) for v in option_categories.values() if v > 0
            )
            st.markdown(
                f"""
                <div style='display:flex;
                justify-content:space-between;
                padding:12px 0;
                margin-top:4px;
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
    # 完整持仓明细
    # ============================================================
    st.markdown(
        "<div class='section-title'>📋 完整持仓明细</div>",
        unsafe_allow_html=True
    )

    st.dataframe(df_positions.copy(), use_container_width=True)


    # ============================================================
    # 📝 交易记录（只有买卖，不含 dividend/deposit/换钱）
    # ============================================================
    st.markdown(
        "<div class='section-title'>📝 交易记录</div>",
        unsafe_allow_html=True
    )

    trades_history = load_trades_history()

    if not trades_history.empty:

        # 筛选
        t_col1, t_col2, t_col3 = st.columns(3)

        with t_col1:
            if "Symbol" in trades_history.columns:
                symbols = ["All"] + sorted(trades_history["Symbol"].dropna().unique().tolist())
                sel_symbol = st.selectbox("Symbol", symbols, key="th_symbol")
            else:
                sel_symbol = "All"

        with t_col2:
            if "AssetClass" in trades_history.columns:
                classes = ["All"] + sorted(trades_history["AssetClass"].dropna().unique().tolist())
                sel_class = st.selectbox("Asset Class", classes, key="th_class")
            else:
                sel_class = "All"

        with t_col3:
            if "Buy/Sell" in trades_history.columns:
                sides = ["All"] + sorted(trades_history["Buy/Sell"].dropna().unique().tolist())
                sel_side = st.selectbox("Buy/Sell", sides, key="th_side")
            else:
                sel_side = "All"

        filtered = trades_history.copy()

        if sel_symbol != "All":
            filtered = filtered[filtered["Symbol"] == sel_symbol]
        if sel_class != "All":
            filtered = filtered[filtered["AssetClass"] == sel_class]
        if sel_side != "All":
            filtered = filtered[filtered["Buy/Sell"] == sel_side]

        st.dataframe(filtered, use_container_width=True, hide_index=True)

        # Realized P&L 汇总
        if "FifoPnlRealized" in filtered.columns:
            rpnl_values = pd.to_numeric(filtered["FifoPnlRealized"], errors="coerce")
            total_rpnl = rpnl_values.sum()
            closed_count = int(rpnl_values.notna().sum())

            s_col1, s_col2, s_col3 = st.columns(3)
            s_col1.metric("Total Realized P&L", f"USD ${total_rpnl:,.2f}")
            s_col2.metric("Closed Trades", f"{closed_count}")
            s_col3.metric("Total Trades", f"{len(filtered)}")

    else:
        st.info("暂无交易记录。上传 CSV 后会自动累加。")

    # ============================================================
    # 💰 Dividends & Deposits 累计（从 portfolio_history.csv 读取）
    # ============================================================
    st.markdown(
        "<div class='section-title'>💰 Dividends & Deposits</div>",
        unsafe_allow_html=True
    )

    cash_summary = load_cash_summary_total()

    if cash_summary and (
        cash_summary["dividends"] != 0
        or cash_summary["withholding_tax"] != 0
        or cash_summary["deposits"] != 0
    ):

        st.markdown(f"""
        <div class='card' style='padding:24px;'>

        <div style='display:grid;
                    grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
                    gap:20px;'>

        <div>
        <div style='color:gray; font-size:13px;'>Total Dividends</div>
        <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
        USD ${cash_summary['dividends']:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Withholding Tax</div>
        <div style='color:#FF6666; font-size:22px; font-weight:bold;'>
        USD ${cash_summary['withholding_tax']:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Net Dividends</div>
        <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
        USD ${cash_summary['net_dividends']:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Total Deposits</div>
        <div style='color:white; font-size:22px; font-weight:bold;'>
        SGD ${cash_summary['deposits']:,.2f}
        </div>
        </div>

        </div>

        </div>
        """, unsafe_allow_html=True)

    else:
        st.info("暂无 Dividend / Deposit 数据")

# ============================================================
# NO DATA
# ============================================================
else:
    st.warning("⚠️ 暂无 IBKR 数据，请先在 Overview 页面上传 CSV 文件。")