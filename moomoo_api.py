"""
Moomoo API → Statement CSV (with simplified GUI)
- Account Overview / Holdings / Trades
- Cash Summary (Deposits / Dividends / WithholdingTax / Fees)
- Options parsed (underlying/strike/expiry/DTE)
- Commission via order_fee_query
- ⭐ Historical FX conversion (frankfurter.app + local cache)
- Background-threaded export to keep GUI responsive
"""

import os
import re
import csv
import json
import time
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta

import pandas as pd
import moomoo as ft


# ============================================================
# CONFIG
# ============================================================
DEFAULT_ACC_ID = "283726802396540551"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111
DEFAULT_USDSGD = 1.34
DEFAULT_HKDSGD = 0.17

OPTION_MULTIPLIER = 100

CASH_FLOW_SLEEP = 3.2  # 10 reqs / 30s rate limit

FX_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fx_cache.json"
)
_FX_CACHE = None  # Lazy-loaded dict

UNIFIED_POSITIONS_COLS = [
    "Platform", "Symbol", "Description", "AssetClass", "Currency",
    "Quantity", "Multiplier", "CostPrice", "ClosePrice",
    "PositionValue", "PositionValueSgd",
    "UnrealizedPnL", "UnrealizedPnLSgd",
    "RealizedPnL", "RealizedPnLSgd",
    "UnderlyingSymbol", "Put/Call", "Strike", "Expiry", "DTE",
]

UNIFIED_TRADES_COLS = [
    "Platform", "TradeDate", "Symbol", "Description", "AssetClass",
    "Buy/Sell", "Quantity", "TradePrice", "Currency",
    "Strategy", "Notes",
    "NetCash", "Commission",
    "RealizedPnL", "RealizedPnLSgd", "UsdToSgd",
]


# ============================================================
# HELPERS
# ============================================================
def safe_float(v, default=0.0):
    try:
        if v is None or pd.isna(v):
            return default
        if isinstance(v, str):
            v = v.replace(",", "").replace("$", "").strip()
            if v == "":
                return default
        return float(v)
    except Exception:
        return default


def safe_str(v, default=""):
    try:
        if v is None or pd.isna(v):
            return default
        return str(v).strip()
    except Exception:
        return default


def get_value(row, cols, default=None):
    for c in cols:
        if c in row.index:
            return row[c]
    return default


def normalize_side(v):
    s = safe_str(v).upper()
    if "BUY" in s or s in ["B", "BOT"]:
        return "BUY"
    if "SELL" in s or s in ["S", "SLD"]:
        return "SELL"
    return s


def normalize_trade_date(v):
    s = safe_str(v)
    if s == "":
        return ""
    if " " in s:
        return s.split(" ")[0]
    return s[:10]


def detect_currency_from_code(code):
    code = safe_str(code).upper()
    if code.startswith("US."):
        return "USD"
    if code.startswith("HK."):
        return "HKD"
    if code.startswith("SG."):
        return "SGD"
    return "USD"


def clean_symbol(code):
    code = safe_str(code)
    if "." in code:
        return code.split(".", 1)[1]
    return code


def call_api(func, **kwargs):
    try:
        return func(**kwargs)
    except TypeError:
        kwargs2 = {k: v for k, v in kwargs.items() if k != "acc_id"}
        return func(**kwargs2)


# ============================================================
# ⭐ HISTORICAL FX (frankfurter.app + cache)
# ============================================================
def _load_fx_cache():
    """Load FX cache from disk."""
    global _FX_CACHE
    if _FX_CACHE is not None:
        return _FX_CACHE
    try:
        if os.path.exists(FX_CACHE_FILE):
            with open(FX_CACHE_FILE, "r", encoding="utf-8") as f:
                _FX_CACHE = json.load(f)
        else:
            _FX_CACHE = {}
    except Exception:
        _FX_CACHE = {}
    return _FX_CACHE


def _save_fx_cache():
    """Save FX cache to disk."""
    try:
        with open(FX_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_FX_CACHE, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save fx_cache.json: {e}")


def _fx_pair_key(from_ccy, to_ccy):
    return f"{from_ccy.upper()}_{to_ccy.upper()}"


def fetch_historical_fx_range(start_date, end_date, from_ccy, to_ccy, log=print):
    """
    Pre-fetch all historical FX rates in [start_date, end_date] in ONE API call.
    Caches results to fx_cache.json.

    Source: https://api.frankfurter.app
    Free, no API key, daily rates back to 1999.
    Note: weekends/holidays have no data — we fallback to nearest earlier weekday.
    """
    cache = _load_fx_cache()
    key = _fx_pair_key(from_ccy, to_ccy)
    if key not in cache:
        cache[key] = {}
    pair_cache = cache[key]

    # Check if range is already cached
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except Exception as e:
        log(f"⚠️ Invalid date for FX fetch: {e}")
        return pair_cache

    # Determine missing weekdays
    missing_weekdays = []
    cur = start
    while cur <= end:
        # skip weekends (no FX market data)
        if cur.weekday() < 5:
            ds = cur.strftime("%Y-%m-%d")
            if ds not in pair_cache:
                missing_weekdays.append(ds)
        cur += timedelta(days=1)

    if not missing_weekdays:
        log(f"   💾 FX cache hit: {from_ccy}→{to_ccy} ({start_date} → {end_date})")
        return pair_cache

    # Fetch from API
    try:
        url = (
            f"https://api.frankfurter.app/"
            f"{start_date}..{end_date}"
            f"?from={from_ccy}&to={to_ccy}"
        )
        log(f"   🌐 Fetching FX: {from_ccy}→{to_ccy} ({start_date} → {end_date}) ...")

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "moomoo_api_export/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        rates = data.get("rates", {})
        for date_str, rate_dict in rates.items():
            r = rate_dict.get(to_ccy)
            if r:
                pair_cache[date_str] = float(r)

        _save_fx_cache()
        log(f"   ✅ Cached {len(rates)} FX data points for {from_ccy}→{to_ccy}")

    except Exception as e:
        log(f"   ⚠️ FX fetch failed: {e}")

    return pair_cache


def get_fx_rate_on_date(date_str, from_ccy, to_ccy, fallback_rate):
    """
    Get FX rate for a specific date.
    Falls back to nearest earlier weekday (up to 7 days back), then fallback_rate.
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()

    if from_ccy == to_ccy:
        return 1.0

    cache = _load_fx_cache()
    pair_cache = cache.get(_fx_pair_key(from_ccy, to_ccy), {})

    if not pair_cache or not date_str:
        return fallback_rate

    # Exact match
    if date_str in pair_cache:
        return pair_cache[date_str]

    # Fallback: search earlier dates (weekends, holidays)
    try:
        target = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        return fallback_rate

    for delta in range(1, 15):
        prev_str = (target - timedelta(days=delta)).strftime("%Y-%m-%d")
        if prev_str in pair_cache:
            return pair_cache[prev_str]

    # Also try forward (in case start of range)
    for delta in range(1, 15):
        next_str = (target + timedelta(days=delta)).strftime("%Y-%m-%d")
        if next_str in pair_cache:
            return pair_cache[next_str]

    return fallback_rate


def convert_to_sgd_on_date(amount, currency, date_str,
                            usd_to_sgd_fixed, hkd_to_sgd_fixed,
                            use_historical=True):
    """Convert amount to SGD using historical FX if available, else fixed."""
    currency = safe_str(currency).upper()

    if currency == "SGD" or amount == 0:
        return amount

    if not use_historical:
        if currency == "USD":
            return amount * usd_to_sgd_fixed
        if currency == "HKD":
            return amount * hkd_to_sgd_fixed
        return amount

    if currency == "USD":
        rate = get_fx_rate_on_date(date_str, "USD", "SGD", usd_to_sgd_fixed)
        return amount * rate
    if currency == "HKD":
        rate = get_fx_rate_on_date(date_str, "HKD", "SGD", hkd_to_sgd_fixed)
        return amount * rate

    return amount


def prefetch_fx_for_range(start_date, end_date, log=print):
    """Pre-fetch USD→SGD and HKD→SGD for the entire date range (incl. today for positions)."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    actual_end = max(end_date, today_str)

    log("📡 Pre-fetching historical FX rates...")
    fetch_historical_fx_range(start_date, actual_end, "USD", "SGD", log=log)
    fetch_historical_fx_range(start_date, actual_end, "HKD", "SGD", log=log)


# ============================================================
# OPTION PARSING
# ============================================================
def parse_option_code(code):
    raw = clean_symbol(code).upper()
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d+)$", raw)
    if not m:
        return None

    underlying = m.group(1)
    yymmdd = m.group(2)
    cp = m.group(3)
    strike_raw = m.group(4)

    try:
        year = "20" + yymmdd[:2]
        month = yymmdd[2:4]
        day = yymmdd[4:6]
        expiry_dt = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
        expiry = expiry_dt.strftime("%Y-%m-%d")
        dte = (expiry_dt - datetime.now()).days
    except Exception:
        expiry = ""
        dte = ""

    put_call = "Call" if cp == "C" else "Put"

    try:
        strike = float(strike_raw) / 1000
    except Exception:
        strike = 0

    return {
        "underlying": underlying, "expiry": expiry,
        "put_call": put_call, "strike": strike, "dte": dte,
    }


def is_option_code(code):
    return parse_option_code(code) is not None


def infer_asset_class(code, name=""):
    if is_option_code(code):
        return "OPT"
    text = f"{safe_str(code)} {safe_str(name)}".upper()
    if "OPTION" in text or " CALL" in text or " PUT" in text:
        return "OPT"
    return "STK"


# ============================================================
# ACCOUNT
# ============================================================
def pick_account(trade_ctx, target_acc_id):
    ret, acc_df = trade_ctx.get_acc_list()
    if ret != ft.RET_OK:
        raise RuntimeError(f"get_acc_list failed: {acc_df}")

    if acc_df.empty:
        raise RuntimeError("No moomoo account found.")

    target_str = str(target_acc_id)
    match = acc_df[acc_df["acc_id"].astype(str) == target_str]

    if not match.empty:
        row = match.iloc[0]
    else:
        print(f"⚠️ acc_id {target_acc_id} not found, using first account.")
        row = acc_df.iloc[0]

    acc_id = row["acc_id"]
    env_str = safe_str(row["trd_env"]).upper()
    trd_env = ft.TrdEnv.SIMULATE if "SIMULATE" in env_str else ft.TrdEnv.REAL

    return acc_id, trd_env


def fetch_account_info(trade_ctx, acc_id, trd_env):
    ret, df = call_api(trade_ctx.accinfo_query, trd_env=trd_env, acc_id=acc_id)
    if ret != ft.RET_OK:
        print("⚠️ accinfo_query failed:", df)
        return pd.DataFrame()
    return df


# ============================================================
# POSITIONS
# ============================================================
def fetch_positions(trade_ctx, acc_id, trd_env):
    ret, df = call_api(trade_ctx.position_list_query, trd_env=trd_env, acc_id=acc_id)
    if ret != ft.RET_OK:
        print("⚠️ position_list_query failed:", df)
        return pd.DataFrame()
    return df


def normalize_positions(pos_df, usd_to_sgd, hkd_to_sgd, use_historical=True):
    """Positions use TODAY's FX rate."""
    if pos_df is None or pos_df.empty:
        return pd.DataFrame(columns=UNIFIED_POSITIONS_COLS)

    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = []

    for _, row in pos_df.iterrows():
        code = get_value(row, ["code", "stock_code", "symbol"], "")
        name = get_value(row, ["stock_name", "name", "description"], "")
        symbol = clean_symbol(code)
        currency = safe_str(get_value(row, ["currency"], "")) or detect_currency_from_code(code)

        qty = safe_float(get_value(row, ["qty", "position", "quantity"], 0))
        cost_price = safe_float(get_value(row, ["average_cost", "cost_price", "avg_cost"], 0))
        close_price = safe_float(get_value(row, ["nominal_price", "last_price", "price", "market_price"], 0))

        position_value = safe_float(
            get_value(row, ["market_val", "market_value", "position_value"], qty * close_price)
        )

        unrealized_pnl = safe_float(get_value(row, ["unrealized_pl", "pl_val", "unrealized_pnl"], 0))
        realized_pnl = safe_float(get_value(row, ["realized_pl", "realized_pnl"], 0))

        position_value_sgd = convert_to_sgd_on_date(
            position_value, currency, today_str, usd_to_sgd, hkd_to_sgd, use_historical
        )
        unrealized_pnl_sgd = convert_to_sgd_on_date(
            unrealized_pnl, currency, today_str, usd_to_sgd, hkd_to_sgd, use_historical
        )
        realized_pnl_sgd = convert_to_sgd_on_date(
            realized_pnl, currency, today_str, usd_to_sgd, hkd_to_sgd, use_historical
        )

        opt = parse_option_code(symbol)
        if opt:
            asset_class = "OPT"
            multiplier = OPTION_MULTIPLIER
            underlying = opt["underlying"]
            put_call = opt["put_call"]
            strike = opt["strike"]
            expiry = opt["expiry"]
            dte = opt["dte"]
        else:
            asset_class = infer_asset_class(code, name)
            multiplier = 1
            underlying = ""
            put_call = ""
            strike = ""
            expiry = ""
            dte = ""

        rows.append({
            "Platform": "Moomoo",
            "Symbol": symbol,
            "Description": safe_str(name),
            "AssetClass": asset_class,
            "Currency": currency,
            "Quantity": qty,
            "Multiplier": multiplier,
            "CostPrice": cost_price,
            "ClosePrice": close_price,
            "PositionValue": position_value,
            "PositionValueSgd": position_value_sgd,
            "UnrealizedPnL": unrealized_pnl,
            "UnrealizedPnLSgd": unrealized_pnl_sgd,
            "RealizedPnL": realized_pnl,
            "RealizedPnLSgd": realized_pnl_sgd,
            "UnderlyingSymbol": underlying,
            "Put/Call": put_call,
            "Strike": strike,
            "Expiry": expiry,
            "DTE": dte,
        })

    return pd.DataFrame(rows, columns=UNIFIED_POSITIONS_COLS)


# ============================================================
# DEALS
# ============================================================
def fetch_history_deals(trade_ctx, acc_id, trd_env, start_date, end_date):
    if not hasattr(trade_ctx, "history_deal_list_query"):
        print("⚠️ history_deal_list_query not available.")
        return pd.DataFrame()

    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"

    candidates = [
        {"start": start_dt, "end": end_dt},
        {"start_time": start_dt, "end_time": end_dt},
        {"begin_time": start_dt, "end_time": end_dt},
    ]

    for kwargs in candidates:
        try:
            ret, df = call_api(
                trade_ctx.history_deal_list_query,
                trd_env=trd_env,
                acc_id=acc_id,
                **kwargs,
            )
            if ret == ft.RET_OK:
                return df
            else:
                print(f"⚠️ history_deal_list_query failed: {df}")
        except TypeError:
            continue

    return pd.DataFrame()


# ============================================================
# ORDER FEES
# ============================================================
def fetch_order_fees(trade_ctx, acc_id, trd_env, order_ids, log=print):
    fees = {}
    order_ids = [str(o) for o in order_ids if str(o).strip()]
    order_ids = list(set(order_ids))

    if not order_ids:
        return fees

    if not hasattr(trade_ctx, "order_fee_query"):
        log("⚠️ order_fee_query not available in this moomoo-api version.")
        return fees

    batch_size = 50
    for i in range(0, len(order_ids), batch_size):
        batch = order_ids[i:i + batch_size]
        try:
            ret, df = call_api(
                trade_ctx.order_fee_query,
                order_id_list=batch,
                trd_env=trd_env,
                acc_id=acc_id,
            )
            if ret != ft.RET_OK:
                log(f"⚠️ order_fee_query batch failed: {df}")
                continue
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                oid = safe_str(get_value(row, ["order_id", "orderID"], ""))
                fee = safe_float(get_value(
                    row, ["fee_amount", "fee_value", "total_fee", "commission", "fee"], 0,
                ))
                if oid:
                    fees[oid] = fees.get(oid, 0) + fee
        except Exception as e:
            log(f"⚠️ order_fee_query exception: {e}")

    return fees


# ============================================================
# CASH FLOW
# ============================================================
def fetch_cash_flow(trade_ctx, acc_id, trd_env, start_date, end_date, log=print):
    if trd_env == ft.TrdEnv.SIMULATE:
        log("⚠️ Cash flow API does not support SIMULATE — skipped.")
        return pd.DataFrame()

    func_name = None
    for cand in ["get_acc_cash_flow", "acc_cash_flow_query"]:
        if hasattr(trade_ctx, cand):
            func_name = cand
            break

    if func_name is None:
        log("⚠️ No cash flow API found in this moomoo-api version.")
        return pd.DataFrame()

    func = getattr(trade_ctx, func_name)

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except Exception as e:
        log(f"⚠️ Invalid date: {e}")
        return pd.DataFrame()

    all_rows = []
    days = (end_dt - start_dt).days + 1
    log(f"🔍 Fetching cash flow for {days} days ...")
    log(f"   (rate-limited: ~{CASH_FLOW_SLEEP}s/day, est. {days * CASH_FLOW_SLEEP / 60:.1f} min)")

    cur = start_dt
    success_count = 0
    fail_count = 0
    consecutive_unsupported = 0

    while cur <= end_dt:
        date_str = cur.strftime("%Y-%m-%d")

        try:
            ret, df = call_api(
                func,
                clearing_date=date_str,
                trd_env=trd_env,
                acc_id=acc_id,
            )

            if ret == ft.RET_OK:
                if df is not None and not df.empty:
                    # Tag every row with its clearing_date (some versions might not include it)
                    if "clearing_date" not in df.columns:
                        df = df.copy()
                        df["clearing_date"] = date_str
                    all_rows.append(df)
                    success_count += 1
                consecutive_unsupported = 0
            else:
                msg = safe_str(df).lower()
                if any(k in msg for k in ["unknown protocol", "not support", "unsupported"]):
                    consecutive_unsupported += 1
                    if consecutive_unsupported >= 3:
                        log("⚠️ Cash flow API not supported for this account — aborting.")
                        break
                fail_count += 1

        except Exception as e:
            fail_count += 1
            log(f"⚠️ {date_str}: {e}")

        if (success_count + fail_count) > 0 and (success_count + fail_count) % 30 == 0:
            log(f"   ... processed {success_count + fail_count}/{days} days "
                f"(found data on {success_count}, failed {fail_count})")

        cur += timedelta(days=1)
        if cur <= end_dt:
            time.sleep(CASH_FLOW_SLEEP)

    if not all_rows:
        log(f"ℹ️ No cash flow records found ({success_count} ok / {fail_count} fail).")
        return pd.DataFrame()

    combined = pd.concat(all_rows, ignore_index=True)
    log(f"✅ Cash flow rows retrieved: {len(combined)}")
    return combined


def categorize_cashflow(cf_type, cf_remark):
    text = f"{safe_str(cf_type)} {safe_str(cf_remark)}".upper()

    if any(k in text for k in [
        "WITHHOLD", "WITHHOLDING TAX", "DIVIDEND TAX", "TAX ON DIVIDEND",
        "预扣税", "預扣稅", "股息税", "股息稅",
    ]):
        return "withholding_tax"

    if any(k in text for k in [
        "DIVIDEND", "DIV", "股息", "派息", "紅利", "红利",
    ]):
        return "dividend"

    if any(k in text for k in [
        "DEPOSIT", "FUNDS TRANSFER IN", "TRANSFER IN", "FUND IN",
        "存入", "入金", "存款", "转入", "轉入",
    ]):
        return "deposit"

    if any(k in text for k in [
        "WITHDRAW", "WITHDRAWAL", "FUNDS TRANSFER OUT", "TRANSFER OUT", "FUND OUT",
        "提取", "出金", "转出", "轉出",
    ]):
        return "withdrawal"

    if any(k in text for k in [
        "COMMISSION", "PLATFORM FEE", "SETTLEMENT FEE", "REGULATORY FEE",
        "CLEARING FEE", "TRADING ACTIVITY FEE", "SEC FEE", "AUDIT FEE",
        "TRANSACTION FEE", "EXCHANGE FEE", "FEE", "GST", "TAX",
        "INTEREST", "MARGIN INTEREST",
        "手续费", "手續費", "佣金", "佣金費", "利息", "费用", "費用",
    ]):
        return "fee"

    return "other"


def aggregate_cash_summary(cash_flow_df, usd_to_sgd, hkd_to_sgd, use_historical=True):
    """Aggregate raw cash flow with date-aware FX conversion."""
    result = {
        "deposits": 0.0, "withdrawals": 0.0, "dividends": 0.0,
        "withholding_tax": 0.0, "net_dividends": 0.0,
        "fees": 0.0, "other": 0.0,
    }

    if cash_flow_df is None or cash_flow_df.empty:
        return result

    for _, row in cash_flow_df.iterrows():
        amt = safe_float(get_value(row, ["cashflow_amount", "amount"], 0))
        currency = safe_str(get_value(row, ["currency"], "USD")).upper()
        cf_type = safe_str(get_value(row, ["cashflow_type", "type"], ""))
        cf_remark = safe_str(get_value(row, ["cashflow_remark", "remark"], ""))
        date_str = safe_str(get_value(row, ["clearing_date", "settlement_date"], ""))

        amt_sgd = convert_to_sgd_on_date(
            amt, currency, date_str, usd_to_sgd, hkd_to_sgd, use_historical
        )
        category = categorize_cashflow(cf_type, cf_remark)

        if category == "deposit" and amt_sgd > 0:
            result["deposits"] += amt_sgd
        elif category == "withdrawal" and amt_sgd < 0:
            result["withdrawals"] += amt_sgd
        elif category == "dividend":
            result["dividends"] += amt_sgd
        elif category == "withholding_tax":
            result["withholding_tax"] += amt_sgd
        elif category == "fee":
            result["fees"] += amt_sgd
        else:
            result["other"] += amt_sgd

    result["net_dividends"] = result["dividends"] + result["withholding_tax"]
    return result


# ============================================================
# NORMALIZE TRADES (with date-aware FX)
# ============================================================
def normalize_trades(deal_df, usd_to_sgd, hkd_to_sgd, order_fees=None, use_historical=True):
    if deal_df is None or deal_df.empty:
        return pd.DataFrame(columns=UNIFIED_TRADES_COLS)

    order_fees = order_fees or {}

    if "order_id" in deal_df.columns:
        oid_counts = deal_df["order_id"].astype(str).value_counts().to_dict()
    else:
        oid_counts = {}

    rows = []
    for _, row in deal_df.iterrows():
        code = get_value(row, ["code", "stock_code", "symbol"], "")
        name = get_value(row, ["stock_name", "name", "description"], "")
        symbol = clean_symbol(code)
        currency = safe_str(get_value(row, ["currency"], "")) or detect_currency_from_code(code)

        side = normalize_side(get_value(row, ["trd_side", "side", "trade_side"], ""))
        qty = safe_float(get_value(row, ["qty", "quantity", "deal_qty"], 0))
        price = safe_float(get_value(row, ["price", "deal_price", "trade_price"], 0))

        opt = parse_option_code(symbol)
        is_opt = opt is not None
        multiplier = OPTION_MULTIPLIER if is_opt else 1

        gross = qty * price * multiplier
        net_cash = -gross if side == "BUY" else gross

        trade_time = get_value(row, ["create_time", "updated_time", "time", "deal_time"], "")
        trade_date = normalize_trade_date(trade_time)

        order_id = safe_str(get_value(row, ["order_id", "orderID"], ""))
        full_fee = order_fees.get(order_id, 0)
        deal_count = oid_counts.get(order_id, 1) or 1
        commission = full_fee / deal_count

        asset_class = "OPT" if is_opt else infer_asset_class(code, name)

        # ⭐ Get historical FX rate for this trade date
        if currency == "USD":
            usd_rate_for_row = get_fx_rate_on_date(trade_date, "USD", "SGD", usd_to_sgd) \
                if use_historical else usd_to_sgd
        else:
            usd_rate_for_row = ""

        rows.append({
            "Platform": "Moomoo",
            "TradeDate": trade_date,
            "Symbol": symbol,
            "Description": safe_str(name),
            "AssetClass": asset_class,
            "Buy/Sell": side,
            "Quantity": qty,
            "TradePrice": price,
            "Currency": currency,
            "Strategy": "",
            "Notes": "",
            "NetCash": net_cash,
            "Commission": commission,
            "RealizedPnL": "",
            "RealizedPnLSgd": "",
            "UsdToSgd": usd_rate_for_row,
        })

    df = pd.DataFrame(rows, columns=UNIFIED_TRADES_COLS)
    df = df.sort_values("TradeDate", ascending=False).reset_index(drop=True)
    return df


# ============================================================
# STATEMENT WRITER
# ============================================================
def write_statement_csv(out_path, acc_info_df, positions_df, trades_df,
                        cash_summary, cash_flow_raw_df,
                        start_date, end_date, usd_to_sgd, hkd_to_sgd,
                        use_historical_fx):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)

        w.writerow(["Moomoo Statement"])
        w.writerow(["GeneratedAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["DateRange", f"{start_date} → {end_date}"])
        w.writerow(["UsdToSgd", usd_to_sgd])
        w.writerow(["HkdToSgd", hkd_to_sgd])
        w.writerow(["FxMode", "Historical" if use_historical_fx else "Fixed"])
        w.writerow([])

        # [Account Overview]
        w.writerow(["[Account Overview]"])
        if acc_info_df is not None and not acc_info_df.empty:
            acc_info_df.to_csv(f, index=False, lineterminator="\n")
        else:
            w.writerow(["No account info."])
        w.writerow([])

        # [Cash Summary]
        w.writerow(["[Cash Summary]"])
        w.writerow(["Item", "Amount(SGD)"])
        w.writerow(["Deposits", round(cash_summary.get("deposits", 0), 2)])
        w.writerow(["Withdrawals", round(cash_summary.get("withdrawals", 0), 2)])
        w.writerow(["Dividends", round(cash_summary.get("dividends", 0), 2)])
        w.writerow(["WithholdingTax", round(cash_summary.get("withholding_tax", 0), 2)])
        w.writerow(["NetDividends", round(cash_summary.get("net_dividends", 0), 2)])
        w.writerow(["Fees", round(cash_summary.get("fees", 0), 2)])
        w.writerow(["Other", round(cash_summary.get("other", 0), 2)])
        w.writerow([])

        # [Cash Flow Raw]
        w.writerow(["[Cash Flow Raw]"])
        if cash_flow_raw_df is not None and not cash_flow_raw_df.empty:
            cash_flow_raw_df.to_csv(f, index=False, lineterminator="\n")
        else:
            w.writerow(["No cash flow data."])
        w.writerow([])

        # [Holdings]
        w.writerow(["[Holdings]"])
        if positions_df is not None and not positions_df.empty:
            positions_df.to_csv(f, index=False, lineterminator="\n")
        else:
            w.writerow(UNIFIED_POSITIONS_COLS)
        w.writerow([])

        # [Trades]
        w.writerow(["[Trades]"])
        if trades_df is not None and not trades_df.empty:
            trades_df.to_csv(f, index=False, lineterminator="\n")
        else:
            w.writerow(UNIFIED_TRADES_COLS)


# ============================================================
# CORE EXPORT
# ============================================================
def run_export(acc_id, start_date, end_date, usd_to_sgd, hkd_to_sgd,
               out_dir, host=DEFAULT_HOST, port=DEFAULT_PORT,
               unlock_pw=None,
               fetch_cash_flow_enabled=True,
               use_historical_fx=True,
               log_callback=print):

    log_callback("====================================")
    log_callback(" Moomoo Statement Export")
    log_callback("====================================")
    log_callback(f"Acc ID     : {acc_id}")
    log_callback(f"Date Range : {start_date} → {end_date} (inclusive)")
    log_callback(f"USDSGD fix : {usd_to_sgd}")
    log_callback(f"HKDSGD fix : {hkd_to_sgd}")
    log_callback(f"FX Mode    : {'HISTORICAL (frankfurter.app)' if use_historical_fx else 'FIXED'}")
    log_callback(f"Cash Flow  : {'ENABLED' if fetch_cash_flow_enabled else 'DISABLED'}")
    log_callback(f"Output Dir : {out_dir}")
    log_callback("====================================")

    # ⭐ Pre-fetch FX rates (one API call for entire range)
    if use_historical_fx:
        try:
            prefetch_fx_for_range(start_date, end_date, log=log_callback)
        except Exception as e:
            log_callback(f"⚠️ FX prefetch failed, falling back to fixed: {e}")
            use_historical_fx = False

    trade_ctx = ft.OpenSecTradeContext(host=host, port=port)

    try:
        chosen_acc_id, trd_env = pick_account(trade_ctx, target_acc_id=acc_id)
        log_callback(f"✅ Using Account: {chosen_acc_id} ({trd_env})")

        if trd_env == ft.TrdEnv.REAL and unlock_pw:
            ret, msg = trade_ctx.unlock_trade(password=unlock_pw)
            log_callback(f"Unlock trade: ret={ret} msg={msg}")

        # Account info
        acc_info_df = fetch_account_info(trade_ctx, chosen_acc_id, trd_env)
        log_callback(f"✅ Account info rows: {len(acc_info_df)}")

        # Positions (use today's FX)
        pos_raw = fetch_positions(trade_ctx, chosen_acc_id, trd_env)
        log_callback(f"✅ Positions raw rows: {len(pos_raw)}")
        positions_df = normalize_positions(
            pos_raw, usd_to_sgd, hkd_to_sgd, use_historical=use_historical_fx
        )

        # History deals
        hist_df = fetch_history_deals(trade_ctx, chosen_acc_id, trd_env, start_date, end_date)
        log_callback(f"✅ History deals: {len(hist_df) if hist_df is not None else 0}")

        combined = hist_df.copy() if hist_df is not None and not hist_df.empty else pd.DataFrame()

        if not combined.empty:
            dedup_cols = [c for c in
                          ["deal_id", "order_id", "code", "create_time", "qty", "price"]
                          if c in combined.columns]
            combined = combined.drop_duplicates(subset=dedup_cols) if dedup_cols else combined.drop_duplicates()

        # Order fees
        order_fees = {}
        if not combined.empty and "order_id" in combined.columns:
            unique_oids = combined["order_id"].astype(str).unique().tolist()
            log_callback(f"🔍 Querying fees for {len(unique_oids)} orders...")
            order_fees = fetch_order_fees(
                trade_ctx, chosen_acc_id, trd_env, unique_oids, log=log_callback
            )
            log_callback(f"✅ Fees retrieved for {len(order_fees)} orders")

        # Trades (use trade date FX)
        trades_df = normalize_trades(
            combined, usd_to_sgd, hkd_to_sgd,
            order_fees=order_fees, use_historical=use_historical_fx
        )

        # Cash flow
        if fetch_cash_flow_enabled:
            cash_flow_raw = fetch_cash_flow(
                trade_ctx, chosen_acc_id, trd_env, start_date, end_date, log=log_callback,
            )
        else:
            log_callback("⚠️ Cash flow fetch DISABLED.")
            cash_flow_raw = pd.DataFrame()

        # Aggregate (use clearing_date FX)
        cash_summary = aggregate_cash_summary(
            cash_flow_raw, usd_to_sgd, hkd_to_sgd, use_historical=use_historical_fx
        )

        log_callback("\n📊 Cash Summary (SGD):")
        log_callback(f"   Deposits      : ${cash_summary['deposits']:>12,.2f}")
        log_callback(f"   Withdrawals   : ${cash_summary['withdrawals']:>12,.2f}")
        log_callback(f"   Dividends     : ${cash_summary['dividends']:>12,.2f}")
        log_callback(f"   Withholding   : ${cash_summary['withholding_tax']:>12,.2f}")
        log_callback(f"   Net Dividends : ${cash_summary['net_dividends']:>12,.2f}")
        log_callback(f"   Fees          : ${cash_summary['fees']:>12,.2f}")
        log_callback(f"   Other         : ${cash_summary['other']:>12,.2f}")

        # Save
        start_tag = start_date.replace("-", "")
        end_tag = end_date.replace("-", "")
        filename = f"moomoo_statement({start_tag}-{end_tag}).csv"
        out_path = os.path.join(out_dir, filename)

        write_statement_csv(
            out_path, acc_info_df, positions_df, trades_df,
            cash_summary, cash_flow_raw,
            start_date, end_date, usd_to_sgd, hkd_to_sgd,
            use_historical_fx,
        )

        log_callback(f"\n✅ Saved: {os.path.abspath(out_path)}")
        log_callback(f"   Holdings : {len(positions_df)} rows")
        log_callback(f"   Trades   : {len(trades_df)} rows")
        log_callback(f"   CashFlow : {len(cash_flow_raw)} rows")

        return out_path

    finally:
        trade_ctx.close()
        log_callback("\n✅ Done.")


# ============================================================
# GUI
# ============================================================
class MoomooGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Moomoo Statement Export")
        self.geometry("600x680")
        self.resizable(False, False)

        pad = {"padx": 10, "pady": 6}

        # Account ID
        ttk.Label(self, text="Account ID:").grid(row=0, column=0, sticky="w", **pad)
        self.acc_var = tk.StringVar(value=DEFAULT_ACC_ID)
        ttk.Entry(self, textvariable=self.acc_var, width=44).grid(row=0, column=1, **pad)

        # Date range
        today = datetime.now()
        default_start = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        default_end = today.strftime("%Y-%m-%d")

        ttk.Label(self, text="Start Date (YYYY-MM-DD):").grid(row=1, column=0, sticky="w", **pad)
        self.start_var = tk.StringVar(value=default_start)
        ttk.Entry(self, textvariable=self.start_var, width=44).grid(row=1, column=1, **pad)

        ttk.Label(self, text="End Date (YYYY-MM-DD):").grid(row=2, column=0, sticky="w", **pad)
        self.end_var = tk.StringVar(value=default_end)
        ttk.Entry(self, textvariable=self.end_var, width=44).grid(row=2, column=1, **pad)

        ttk.Label(self, text="(both dates inclusive)",
                  foreground="gray").grid(row=3, column=1, sticky="w", padx=10)

        # FX rates
        ttk.Label(self, text="USD → SGD (fallback):").grid(row=4, column=0, sticky="w", **pad)
        self.usdsgd_var = tk.StringVar(value=str(DEFAULT_USDSGD))
        ttk.Entry(self, textvariable=self.usdsgd_var, width=44).grid(row=4, column=1, **pad)

        ttk.Label(self, text="HKD → SGD (fallback):").grid(row=5, column=0, sticky="w", **pad)
        self.hkdsgd_var = tk.StringVar(value=str(DEFAULT_HKDSGD))
        ttk.Entry(self, textvariable=self.hkdsgd_var, width=44).grid(row=5, column=1, **pad)

        # Output folder
        ttk.Label(self, text="Output Folder:").grid(row=6, column=0, sticky="w", **pad)
        self.outdir_var = tk.StringVar(value=os.getcwd())
        frm = ttk.Frame(self)
        frm.grid(row=6, column=1, **pad)
        ttk.Entry(frm, textvariable=self.outdir_var, width=34).pack(side="left")
        ttk.Button(frm, text="...", command=self.choose_dir, width=4).pack(side="left", padx=4)

        # ⭐ Historical FX toggle
        self.histfx_var = tk.BooleanVar(value=True)
        fx_frame = ttk.Frame(self)
        fx_frame.grid(row=7, column=1, sticky="w", **pad)
        ttk.Checkbutton(
            fx_frame,
            text="Use historical FX rates (frankfurter.app, cached)",
            variable=self.histfx_var,
        ).pack(side="left")

        # Cash Flow toggle
        self.cashflow_var = tk.BooleanVar(value=True)
        cf_frame = ttk.Frame(self)
        cf_frame.grid(row=8, column=1, sticky="w", **pad)
        ttk.Checkbutton(
            cf_frame,
            text="Fetch Cash Flow (Deposits/Dividends/Tax/Fees)",
            variable=self.cashflow_var,
        ).pack(side="left")

        ttk.Label(
            self,
            text="(Cash flow query is rate-limited ~3s/day → 365 days ≈ 18min)",
            foreground="gray",
            font=("Consolas", 8),
        ).grid(row=9, column=1, sticky="w", padx=10)

        # Export button
        self.export_btn = ttk.Button(self, text="Export Statement", command=self.on_export)
        self.export_btn.grid(row=10, column=0, columnspan=2, pady=12)

        # Log
        self.log_text = tk.Text(self, height=17, width=70, font=("Consolas", 9))
        self.log_text.grid(row=11, column=0, columnspan=2, padx=10, pady=8)

        self._worker = None

    def log(self, msg):
        self.log_text.insert("end", str(msg) + "\n")
        self.log_text.see("end")
        self.update_idletasks()

    def safe_log(self, msg):
        self.after(0, lambda: self.log(msg))

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get())
        if d:
            self.outdir_var.set(d)

    def on_export(self):
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("Already running", "An export is already in progress.")
            return

        try:
            acc_id = self.acc_var.get().strip()
            start_date = self.start_var.get().strip()
            end_date = self.end_var.get().strip()
            usd_to_sgd = float(self.usdsgd_var.get().strip())
            hkd_to_sgd = float(self.hkdsgd_var.get().strip())
            out_dir = self.outdir_var.get().strip()
            cashflow_enabled = self.cashflow_var.get()
            histfx_enabled = self.histfx_var.get()

            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except Exception as e:
            messagebox.showerror("Invalid input", str(e))
            return

        self.log_text.delete("1.0", "end")
        self.export_btn.config(state="disabled", text="Exporting...")

        def worker():
            try:
                out_path = run_export(
                    acc_id=acc_id,
                    start_date=start_date,
                    end_date=end_date,
                    usd_to_sgd=usd_to_sgd,
                    hkd_to_sgd=hkd_to_sgd,
                    out_dir=out_dir,
                    fetch_cash_flow_enabled=cashflow_enabled,
                    use_historical_fx=histfx_enabled,
                    log_callback=self.safe_log,
                )
                self.after(0, lambda: messagebox.showinfo("Success", f"Saved:\n{out_path}"))
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self.safe_log(f"\n❌ Error: {err}"))
                self.after(0, lambda: messagebox.showerror("Export failed", err))
            finally:
                self.after(0, lambda: self.export_btn.config(state="normal", text="Export Statement"))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()


# ============================================================
if __name__ == "__main__":
    app = MoomooGUI()
    app.mainloop()