import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from datetime import datetime

st.set_page_config(page_title="Tiger", page_icon="🐯", layout="wide")

from app import (
    require_auth, load_css, get_history_file, format_df,  get_user_dir,
    get_trades_history_file, OPTION_COLORS,
    detect_coverage_gaps,
)

from tiger import (
    load_latest_snapshot, process_incoming, analyze_positions,
    load_trades_history,
    load_cash_summary_total_sgd,
    load_realized_pnl_summary_sgd,
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
# CACHED LOADERS
# ============================================================
def _get_history_mtime():
    if os.path.exists(get_history_file()):
        return os.path.getmtime(get_history_file())
    return 0

def _get_trades_mtime():
    if os.path.exists(get_trades_history_file()):
        return os.path.getmtime(get_trades_history_file())
    return 0


@st.cache_data(ttl=300)
def tiger_cached_load_latest_snapshot(mtime):
    return load_latest_snapshot()

@st.cache_data(ttl=300)
def tiger_cached_load_trades_history(mtime):
    return load_trades_history()

@st.cache_data(ttl=300)
def tiger_cached_load_cash_summary_total_sgd(mtime):
    return load_cash_summary_total_sgd()

@st.cache_data(ttl=300)
def tiger_cached_load_realized_pnl_summary_sgd(mtime):
    return load_realized_pnl_summary_sgd()

# ============================================================
# LOAD DATA
# ============================================================
history_mtime = _get_history_mtime()
trades_mtime = _get_trades_mtime()

df_positions = None
total_nav = 0
cash = 0
real_pnl = 0
total_deposit = 0
total_withdrawal = 0
total_other = 0
usd_to_sgd_current = 1.34

loaded = tiger_cached_load_latest_snapshot(history_mtime)


if loaded is not None:
    df_positions = loaded["df_positions"]
    usd_to_sgd_current = loaded.get("usd_to_sgd", 1.34)

    total_nav = float(loaded["nav"])
    cash = float(loaded["cash"])
    real_pnl = float(loaded["pnl"])
    total_deposit = float(loaded["deposit"])
    total_withdrawal = float(loaded.get("withdrawal", 0))
    total_other = float(loaded.get("other", 0))

# ⭐ Net Capital = Deposit + Withdrawal (withdrawal is negative)
net_capital = total_deposit + total_withdrawal

# Realized P&L SGD
realized_summary_sgd = tiger_cached_load_realized_pnl_summary_sgd(trades_mtime)
realized_profit = realized_summary_sgd["realized_profit"]
realized_loss = realized_summary_sgd["realized_loss"]

# Dividends / Deposits SGD
cash_summary_sgd = tiger_cached_load_cash_summary_total_sgd(history_mtime)

# ============================================================
# PAGE TITLE
# ============================================================
st.title("🐯 Tiger Portfolio")

st.markdown(
    f"<div style='color:gray; font-size:13px; margin-top:-10px; margin-bottom:14px;'>"
    f"当前汇率 USD → SGD: {usd_to_sgd_current:.4f} | SGD 仓位直读，USD 仓位按汇率转换"
    f"</div>",
    unsafe_allow_html=True
)

# ============================================================
# Tiger Summary Card
# ============================================================
cash_pct = (cash / total_nav * 100) if total_nav != 0 else 0
portfolio_return = total_nav - net_capital
return_pct = (portfolio_return / net_capital * 100) if net_capital != 0 else 0
return_color = "#66FF99" if portfolio_return >= 0 else "#FF6666"
pnl_color = "#66FF99" if real_pnl >= 0 else "#FF6666"
nc_color = "#66FF99" if net_capital >= 0 else "#FF6666"

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
SGD ${cash:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Holding P&L</div>
<div style='color:{pnl_color}; font-size:24px; font-weight:bold;'>
SGD ${real_pnl:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Net Capital</div>
<div style='color:{nc_color}; font-size:24px; font-weight:bold;'>
SGD ${net_capital:,.2f}
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
SGD ${realized_profit:,.2f}
</div>
</div>

<div>
<div style='color:gray; font-size:13px;'>Cumulative Realized Loss</div>
<div style='color:#FF6666; font-size:22px; font-weight:bold;'>
SGD ${realized_loss:,.2f}
</div>
</div>

</div>

</div>
""", unsafe_allow_html=True)

# ============================================================
# ANALYZE POSITIONS
# ============================================================
analysis = analyze_positions(df_positions, total_nav, cash)

index_etf_positions = analysis["index_etf_positions"]
stock_positions = analysis["stock_positions"]
option_categories = analysis["option_categories"]
option_positions = analysis["option_positions"]
fx_ratio = analysis["fx_ratio"]

if df_positions is not None and not df_positions.empty:

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

            fig2 = go.Figure(data=[go.Pie(
                labels=etf_labels, values=etf_values, hole=0.65,
                textinfo="label+percent",
                textfont=dict(color="white", size=14),
                showlegend=False
            )])
            fig2.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827", font_color="white")
            st.plotly_chart(fig2, width="stretch")

        with col2:

            st.markdown("### 💰 ETF Holdings")

            for p in index_etf_positions:
                value_sgd = p["Value"]
                pct = (value_sgd / total_nav * 100) if total_nav != 0 else 0

                st.markdown(
                    f"""
                    <div style='display:flex; justify-content:space-between;
                    padding:12px 0; border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap; gap:8px;'>
                    <span style='font-weight:bold; color:black;'>{p['Symbol']}</span>
                    <span style='color:#666;'>SGD ${value_sgd:,.2f} ({pct:.1f}%)</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            etf_total_sgd = sum(p["Value"] for p in index_etf_positions)
            st.markdown(
                f"""
                <div style='display:flex; justify-content:space-between;
                padding:12px 0; margin-top:4px;
                flex-wrap:wrap; gap:8px;'>
                <span style='font-weight:bold; color:black; font-size:18px;'>Total</span>
                <span style='font-weight:bold; color:black; font-size:18px;'>SGD ${etf_total_sgd:,.2f}</span>
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

            fig3 = go.Figure(data=[go.Pie(
                labels=stock_labels, values=stock_values, hole=0.65,
                textinfo="label+percent",
                textfont=dict(color="white", size=14),
                showlegend=False
            )])
            fig3.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827", font_color="white")
            st.plotly_chart(fig3, width="stretch")

        with col2:

            st.markdown("### 💰 Stock Holdings")

            for p in stock_positions:
                value_sgd = p["Value"]
                pct = (value_sgd / total_nav * 100) if total_nav != 0 else 0

                st.markdown(
                    f"""
                    <div style='display:flex; justify-content:space-between;
                    padding:12px 0; border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap; gap:8px;'>
                    <span style='font-weight:bold; color:black;'>{p['Symbol']}</span>
                    <span style='color:#666;'>SGD ${value_sgd:,.2f} ({pct:.1f}%)</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            stock_total_sgd = sum(p["Value"] for p in stock_positions)
            st.markdown(
                f"""
                <div style='display:flex; justify-content:space-between;
                padding:12px 0; margin-top:4px;
                flex-wrap:wrap; gap:8px;'>
                <span style='font-weight:bold; color:black; font-size:18px;'>Total</span>
                <span style='font-weight:bold; color:black; font-size:18px;'>SGD ${stock_total_sgd:,.2f}</span>
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

            option_labels = [k for k, v in option_categories.items() if v > 0]
            option_values = [v for k, v in option_categories.items() if v > 0]
            option_colors = [OPTION_COLORS.get(k, "#9CA3AF") for k, v in option_categories.items() if v > 0]

            fig4 = go.Figure(data=[go.Pie(
                labels=option_labels, values=option_values, hole=0.65,
                marker_colors=option_colors,
                textinfo="label+percent",
                textfont=dict(color="white", size=14),
                showlegend=False
            )])
            fig4.update_layout(paper_bgcolor="#111827", plot_bgcolor="#111827", font_color="white")
            st.plotly_chart(fig4, width="stretch")

        with col2:

            st.markdown("### 💰 Options Holdings")

            for category, exposure in option_categories.items():
                if exposure <= 0:
                    continue

                st.markdown(
                    f"""
                    <div style='display:flex; justify-content:space-between;
                    padding:12px 0; border-bottom:1px solid #E5E7EB;
                    flex-wrap:wrap; gap:8px;'>
                    <span style='font-weight:bold; color:black;'>{category}</span>
                    <span style='color:#666;'>SGD ${exposure:,.2f}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            option_total_sgd = sum(v for v in option_categories.values() if v > 0)
            st.markdown(
                f"""
                <div style='display:flex; justify-content:space-between;
                padding:12px 0; margin-top:4px;
                flex-wrap:wrap; gap:8px;'>
                <span style='font-weight:bold; color:black; font-size:18px;'>Total</span>
                <span style='font-weight:bold; color:black; font-size:18px;'>SGD ${option_total_sgd:,.2f}</span>
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

    positions_display = format_df(
        df_positions,
        cols_2dp=["Quantity", "Multiplier", "CostPrice", "ClosePrice",
                "PositionValue", "PositionValueSgd",
                "UnrealizedPnL", "UnrealizedPnLSgd"],
    )
    st.dataframe(positions_display, width="stretch", hide_index=True)

    # ============================================================
    # 📝 Recent Trades (read-only)
    # ============================================================
    st.markdown(
        "<div class='section-title'>📝 Recent Trades</div>",
        unsafe_allow_html=True
    )

    trades_history = tiger_cached_load_trades_history(trades_mtime)

    if not trades_history.empty:

        t_col1, t_col2, t_col3 = st.columns(3)

        with t_col1:
            if "Symbol" in trades_history.columns:
                symbols = ["All"] + sorted(trades_history["Symbol"].dropna().unique().tolist())
                sel_symbol = st.selectbox("Symbol", symbols, key="tiger_th_symbol")
            else:
                sel_symbol = "All"

        with t_col2:
            if "AssetClass" in trades_history.columns:
                classes = ["All"] + sorted(trades_history["AssetClass"].dropna().unique().tolist())
                sel_class = st.selectbox("Asset Class", classes, key="tiger_th_class")
            else:
                sel_class = "All"

        with t_col3:
            if "Buy/Sell" in trades_history.columns:
                sides = ["All"] + sorted(trades_history["Buy/Sell"].dropna().unique().tolist())
                sel_side = st.selectbox("Buy/Sell", sides, key="tiger_th_side")
            else:
                sel_side = "All"

        filtered = trades_history.copy()

        if sel_symbol != "All":
            filtered = filtered[filtered["Symbol"] == sel_symbol]
        if sel_class != "All":
            filtered = filtered[filtered["AssetClass"] == sel_class]
        if sel_side != "All":
            filtered = filtered[filtered["Buy/Sell"] == sel_side]

        display_cols = [
            c for c in [
                "TradeDate", "Symbol", "Description", "AssetClass",
                "Buy/Sell", "Quantity", "TradePrice", "NetCash",
                "RealizedPnLSgd", "Group",
            ] if c in filtered.columns
        ]

        st.dataframe(
            filtered[display_cols],
            width="stretch",
            hide_index=True,
        )

        st.page_link(
            "pages/5_Trade_Journal.py",
            label="📝 Journal & campaigns → Trade Journal page",
        )

        # ============================================================
        # 📈 Trading Performance (using RealizedPnLSgd)
        # ============================================================

        pnl_col = None
        if "RealizedPnLSgd" in filtered.columns:
            pnl_col = "RealizedPnLSgd"
        elif "RealizedPnL" in filtered.columns:
            pnl_col = "RealizedPnL"

        if pnl_col is not None:

            rpnl_values = pd.to_numeric(filtered[pnl_col], errors="coerce")

            closed_trades = rpnl_values.dropna()
            closed_trades = closed_trades[closed_trades != 0]

            total_trades = len(filtered)
            closed_count = len(closed_trades)

            if closed_count > 0:
                wins = closed_trades[closed_trades > 0]
                losses = closed_trades[closed_trades < 0]

                win_count = len(wins)
                loss_count = len(losses)

                win_rate = (win_count / closed_count * 100) if closed_count > 0 else 0

                avg_win = wins.mean() if win_count > 0 else 0
                avg_loss = losses.mean() if loss_count > 0 else 0

                total_wins = wins.sum()
                total_losses = abs(losses.sum())
                profit_factor = (total_wins / total_losses) if total_losses != 0 else float("inf")

                best_trade = closed_trades.max() if closed_count > 0 else 0
                worst_trade = closed_trades.min() if closed_count > 0 else 0

                total_rpnl = closed_trades.sum()

                wr_color = "#66FF99" if win_rate >= 50 else "#FF6666"
                pf_color = "#66FF99" if profit_factor >= 1.5 else ("#FFC300" if profit_factor >= 1 else "#FF6666")

                pf_display = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"

                st.markdown(f"""
                <div class='card' style='padding:24px; margin-top:16px;'>

                <div style='color:gray; font-size:13px; margin-bottom:16px;'>📈 Trading Performance</div>

                <div style='display:grid;
                            grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
                            gap:20px;
                            margin-bottom:16px;'>

                <div>
                <div style='color:gray; font-size:13px;'>Win Rate</div>
                <div style='color:{wr_color}; font-size:22px; font-weight:bold;'>
                {win_rate:.1f}%
                </div>
                <div style='color:gray; font-size:11px;'>{win_count}W / {loss_count}L</div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Profit Factor</div>
                <div style='color:{pf_color}; font-size:22px; font-weight:bold;'>
                {pf_display}
                </div>
                <div style='color:gray; font-size:11px;'>Wins / Losses</div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Avg Win</div>
                <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
                +SGD ${avg_win:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Avg Loss</div>
                <div style='color:#FF6666; font-size:22px; font-weight:bold;'>
                SGD ${avg_loss:,.2f}
                </div>
                </div>

                </div>

                <div style='border-top:1px solid #333;
                            padding-top:16px;
                            display:grid;
                            grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
                            gap:20px;'>

                <div>
                <div style='color:gray; font-size:13px;'>Best Trade</div>
                <div style='color:#66FF99; font-size:20px; font-weight:bold;'>
                +SGD ${best_trade:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Worst Trade</div>
                <div style='color:#FF6666; font-size:20px; font-weight:bold;'>
                SGD ${worst_trade:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Total Realized P&L</div>
                <div style='color:{"#66FF99" if total_rpnl >= 0 else "#FF6666"}; font-size:20px; font-weight:bold;'>
                SGD ${total_rpnl:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Closed / Total</div>
                <div style='color:white; font-size:20px; font-weight:bold;'>
                {closed_count} / {total_trades}
                </div>
                </div>

                </div>

                </div>
                """, unsafe_allow_html=True)

            else:
                s_col1, s_col2 = st.columns(2)
                s_col1.metric("Total Trades", f"{total_trades}")
                s_col2.metric("Closed Trades", f"{closed_count}")
                st.info("暂无关仓交易记录，所以没有 Win Rate 统计。")

    else:
        st.info("No trades yet.")

    # ============================================================
    # 💰 Dividends & Capital (3 + 4 布局)
    # ============================================================
    st.markdown(
        "<div class='section-title'>💰 Dividends & Capital</div>",
        unsafe_allow_html=True
    )

    cs_dividends = cash_summary_sgd.get("dividends", 0) if cash_summary_sgd else 0
    cs_wht = cash_summary_sgd.get("withholding_tax", 0) if cash_summary_sgd else 0
    cs_netdiv = cash_summary_sgd.get("net_dividends", 0) if cash_summary_sgd else 0
    cs_deposits = cash_summary_sgd.get("deposits", 0) if cash_summary_sgd else total_deposit
    cs_withdraw = cash_summary_sgd.get("withdrawals", 0) if cash_summary_sgd else total_withdrawal
    cs_other = cash_summary_sgd.get("other", 0) if cash_summary_sgd else total_other

    cs_netcapital = cs_deposits + cs_withdraw  # withdrawal 是负数

    if cash_summary_sgd and (
        cs_dividends != 0 or cs_wht != 0 or cs_deposits != 0
        or cs_withdraw != 0 or cs_other != 0
    ):

        netcap_color = "#66FF99" if cs_netcapital >= 0 else "#FF6666"
        other_color = "#66FF99" if cs_other >= 0 else "#FF6666"

        st.markdown(f"""
        <div class='card' style='padding:24px;'>

        <!-- 第一行: Dividends 组 (3 张卡) -->
        <div style='display:grid;
                    grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
                    gap:20px;
                    margin-bottom:16px;'>

        <div>
        <div style='color:gray; font-size:13px;'>Total Dividends</div>
        <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
        SGD ${cs_dividends:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Withholding Tax</div>
        <div style='color:#FF6666; font-size:22px; font-weight:bold;'>
        SGD ${cs_wht:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Net Dividends</div>
        <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
        SGD ${cs_netdiv:,.2f}
        </div>
        </div>

        </div>

        <!-- 第二行: Capital + Net Others (4 张卡) -->
        <div style='border-top:1px solid #333;
                    padding-top:16px;
                    display:grid;
                    grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
                    gap:20px;'>

        <div>
        <div style='color:gray; font-size:13px;'>Total Deposit</div>
        <div style='color:#66FF99; font-size:22px; font-weight:bold;'>
        SGD ${cs_deposits:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Total Withdrawals</div>
        <div style='color:#FF6666; font-size:22px; font-weight:bold;'>
        SGD ${cs_withdraw:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Net Capital</div>
        <div style='color:{netcap_color}; font-size:22px; font-weight:bold;'>
        SGD ${cs_netcapital:,.2f}
        </div>
        </div>

        <div>
        <div style='color:gray; font-size:13px;'>Net Others</div>
        <div style='color:{other_color}; font-size:22px; font-weight:bold;'>
        SGD ${cs_other:,.2f}
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
    st.warning("⚠️ 暂无 Tiger 数据，请先在 Overview 页面上传 Tiger Statement CSV。")