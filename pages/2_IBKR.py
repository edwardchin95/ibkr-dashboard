import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from datetime import datetime

st.set_page_config(page_title="IBKR", page_icon="🟧", layout="wide")

from app import require_auth, load_css, HISTORY_FILE
from ibkr import (
    load_latest_snapshot, process_incoming, analyze_positions,
    load_trades_history,
    load_cash_summary_total,
    load_realized_pnl_summary,
    TRADES_HISTORY_FILE,
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
# CACHED LOADERS（用文件 mtime 当 cache key，文件改了自动失效）
# ============================================================
def _get_history_mtime():
    if os.path.exists(HISTORY_FILE):
        return os.path.getmtime(HISTORY_FILE)
    return 0

def _get_trades_mtime():
    if os.path.exists(TRADES_HISTORY_FILE):
        return os.path.getmtime(TRADES_HISTORY_FILE)
    return 0


@st.cache_data(ttl=300)
def cached_load_latest_snapshot(mtime):
    return load_latest_snapshot()

@st.cache_data(ttl=300)
def cached_load_trades_history(mtime):
    return load_trades_history()

@st.cache_data(ttl=300)
def cached_load_cash_summary_total(mtime):
    return load_cash_summary_total()

@st.cache_data(ttl=300)
def cached_load_realized_pnl_summary(mtime):
    return load_realized_pnl_summary()

# ============================================================
# LOAD DATA
# ============================================================
history_mtime = _get_history_mtime()
trades_mtime = _get_trades_mtime()

df_positions = None
total_nav = 0
cash_sgd = 0
real_pnl = 0
total_deposit = 0

loaded = cached_load_latest_snapshot(history_mtime)

if loaded is not None:
    df_positions = loaded["df_positions"]
    total_nav = loaded["nav"]
    cash_sgd = loaded["cash"]
    real_pnl = loaded["pnl"]
    total_deposit = loaded["deposit"]

# ============================================================
# REALIZED PROFIT / LOSS
# ============================================================
realized_summary = cached_load_realized_pnl_summary(trades_mtime)
realized_profit = realized_summary["realized_profit"]
realized_loss = realized_summary["realized_loss"]

# ============================================================
# PAGE TITLE
# ============================================================
st.title("🟧 IBKR Portfolio")

# ============================================================
# IBKR Summary Card
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

# ============================================================
# 🚨 OPTIONS 到期警报
# ============================================================
if df_positions is not None and len(option_positions) > 0:

    expiring_7d = []
    expiring_14d = []
    expiring_30d = []

    for op in option_positions:
        dte = op.get("DTE")
        if dte is None:
            continue
        if dte < 0:
            continue
        if dte <= 7:
            expiring_7d.append(op)
        elif dte <= 14:
            expiring_14d.append(op)
        elif dte <= 30:
            expiring_30d.append(op)

    if expiring_7d or expiring_14d or expiring_30d:

        st.markdown(
            "<div class='section-title'>⚠️ Options 到期警报</div>",
            unsafe_allow_html=True
        )

        def render_expiry_group(title, items, accent_color):
            if not items:
                return

            rows_html = ""
            for op in sorted(items, key=lambda x: x.get("DTE", 999)):
                dte = op.get("DTE", "?")
                category = op.get("Category", "Other")
                underlying = op.get("Underlying", "")
                strike = op.get("Strike", "")
                qty = op.get("Quantity", 0)

                try:
                    strike_str = f"${float(strike):,.0f}"
                except:
                    strike_str = str(strike)

                rows_html += (
                    f"<div style='display:flex; justify-content:space-between; padding:10px 0; "
                    f"border-bottom:1px solid #2A2A2A; flex-wrap:wrap; gap:8px;'>"
                    f"<div>"
                    f"<span style='color:white; font-weight:bold;'>{underlying}</span>"
                    f"<span style='color:gray; margin-left:8px;'>{category} {strike_str}</span>"
                    f"</div>"
                    f"<div style='text-align:right;'>"
                    f"<span style='color:{accent_color}; font-weight:bold;'>{dte}d</span>"
                    f"<span style='color:gray; margin-left:8px;'>x{int(abs(qty))}</span>"
                    f"</div>"
                    f"</div>"
                )

            card_html = (
                f"<div class='card' style='padding:20px; border-left:4px solid {accent_color};'>"
                f"<div style='color:{accent_color}; font-weight:bold; margin-bottom:12px; font-size:16px;'>"
                f"{title} ({len(items)})"
                f"</div>"
                f"{rows_html}"
                f"</div>"
            )

            st.markdown(card_html, unsafe_allow_html=True)

        render_expiry_group("🔴 7 天内到期", expiring_7d, "#FF6666")
        render_expiry_group("🟡 8-14 天到期", expiring_14d, "#FFC300")
        render_expiry_group("🟢 15-30 天到期", expiring_30d, "#00D4AA")

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
    # 📝 交易记录
    # ============================================================
    st.markdown(
        "<div class='section-title'>📝 交易记录</div>",
        unsafe_allow_html=True
    )

    trades_history = cached_load_trades_history(trades_mtime)

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

        # ============================================================
        # 📈 Trading Performance
        # ============================================================
        if "FifoPnlRealized" in filtered.columns:

            rpnl_values = pd.to_numeric(filtered["FifoPnlRealized"], errors="coerce")

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
                +${avg_win:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Avg Loss</div>
                <div style='color:#FF6666; font-size:22px; font-weight:bold;'>
                ${avg_loss:,.2f}
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
                +${best_trade:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Worst Trade</div>
                <div style='color:#FF6666; font-size:20px; font-weight:bold;'>
                ${worst_trade:,.2f}
                </div>
                </div>

                <div>
                <div style='color:gray; font-size:13px;'>Total Realized P&L</div>
                <div style='color:{"#66FF99" if total_rpnl >= 0 else "#FF6666"}; font-size:20px; font-weight:bold;'>
                USD ${total_rpnl:,.2f}
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
        st.info("暂无交易记录。上传 CSV 后会自动累加。")

    # ============================================================
    # 💰 Dividends & Deposits 累计
    # ============================================================
    st.markdown(
        "<div class='section-title'>💰 Dividends & Deposits</div>",
        unsafe_allow_html=True
    )

    cash_summary = cached_load_cash_summary_total(history_mtime)

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