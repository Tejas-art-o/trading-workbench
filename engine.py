"""Trading workbench engine: data fetch, indicators, rule-based scoring, risk sizing.

Decision-support only. It never places orders.
"""
from __future__ import annotations

import json
import math
import os
from copy import deepcopy

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(HERE, "rules.json")
SIGNAL_LOG = os.path.join(HERE, "signals_log.csv")
JOURNAL_FILE = os.path.join(HERE, "journal.csv")
TZ = "Asia/Kolkata"

LABELS = {
    "ema_trend": "Price trend (EMA)",
    "rsi": "RSI",
    "macd": "MACD",
    "vwap": "VWAP position",
    "candle": "Candlestick pattern",
    "volume": "Volume confirmation",
    "support_resistance": "Support / resistance",
    "market_trend": "Market trend (benchmark)",
}

# Starting assumptions, NOT proven weights. Test them with the signal log / backtests.
DEFAULT_RULES = {
    "weights": {
        "ema_trend": 15, "rsi": 10, "macd": 10, "vwap": 10,
        "candle": 15, "volume": 15, "support_resistance": 10, "market_trend": 15,
    },
    "buy_threshold": 35,
    "sell_threshold": -35,
    "conflict_ratio": 0.30,   # NO TRADE if both bull and bear share of weight >= this
    "adx_min": 18,            # below this the market is treated as choppy
    "block_choppy": True,
    "atr_sl_mult": 1.5,       # stop distance = ATR x this
    "rr1": 1.5,               # target 1 = risk x this
    "rr2": 2.5,               # target 2 = risk x this
    "capital": 100000,
    "risk_pct": 1.0,          # % of capital risked per trade
    "leverage": 1.0,          # max position value = capital x leverage
    "lot_size": 1,
    "max_trades_per_day": 3,
    "daily_loss_limit_pct": 3.0,
    "closed_candles_only": True,
    # used by monitor.py
    "watchlist": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "^NSEI", "^NSEBANK"],
    "interval": "15m",
    "benchmark": "^NSEI",
    "poll_seconds": 120,
}

INTERVAL_MIN = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1d": 1440}
PERIOD = {"1m": "5d", "2m": "5d", "5m": "30d", "15m": "60d", "30m": "60d", "60m": "180d", "1d": "2y"}


# ----------------------------------------------------------------- rules
def load_rules() -> dict:
    rules = deepcopy(DEFAULT_RULES)
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE) as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == "weights" and isinstance(v, dict):
                    rules["weights"].update(v)
                elif k in rules:
                    rules[k] = v
        except Exception:
            pass
    return rules


def save_rules(rules: dict) -> None:
    with open(RULES_FILE, "w") as f:
        json.dump(rules, f, indent=2)


# ------------------------------------------------------------------ data
def fetch(symbol: str, interval: str = "15m", closed_only: bool = True) -> pd.DataFrame:
    """Free OHLCV from Yahoo Finance via yfinance. May be delayed; verify before acting."""
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=PERIOD[interval], interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"No data returned for {symbol} ({interval})")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["open", "high", "low", "close"])
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(TZ)
    if closed_only and interval != "1d" and len(df) > 1:
        now = pd.Timestamp.now(tz=TZ)
        if df.index[-1] + pd.Timedelta(minutes=INTERVAL_MIN[interval]) > now:
            df = df.iloc[:-1]  # drop the still-forming candle
    return df


# ------------------------------------------------------------ indicators
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(dn != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df.high.diff()
    dn = -df.low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n).replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    d = df.copy()
    for n in (9, 20, 50, 200):
        d[f"ema{n}"] = ema(d.close, n)
    d["rsi"] = rsi(d.close)
    fast, slow = ema(d.close, 12), ema(d.close, 26)
    d["macd"] = fast - slow
    d["macd_signal"] = ema(d["macd"], 9)
    d["macd_hist"] = d["macd"] - d["macd_signal"]
    d["atr"] = atr(d)
    d["adx"] = adx(d)
    mid = d.close.rolling(20).mean()
    sd = d.close.rolling(20).std()
    d["bb_up"], d["bb_mid"], d["bb_low"] = mid + 2 * sd, mid, mid - 2 * sd
    d["volma"] = d.volume.rolling(20).mean()
    if interval == "1d":
        d["vwap"] = np.nan  # session VWAP is meaningless on daily bars
    else:
        tp = (d.high + d.low + d.close) / 3
        day = d.index.date
        pv = (tp * d.volume).groupby(day).cumsum()
        v = d.volume.groupby(day).cumsum()
        d["vwap"] = pv / v.replace(0, np.nan)
    return d


def swing_levels(df: pd.DataFrame, k: int = 5, lookback: int = 200):
    """Confirmed swing lows (support) and highs (resistance)."""
    d = df.iloc[-lookback:]
    hi, lo = d.high.values, d.low.values
    sup, res = [], []
    for i in range(k, len(d) - k):
        if hi[i] == hi[i - k:i + k + 1].max():
            res.append(float(hi[i]))
        if lo[i] == lo[i - k:i + k + 1].min():
            sup.append(float(lo[i]))
    return sup, res


def candle_pattern(df: pd.DataFrame):
    """Returns (score -1..+1, name) for the latest closed candle(s)."""
    if len(df) < 8:
        return 0.0, "not enough candles"
    c0, c1, c2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    def parts(c):
        body = abs(c.close - c.open)
        rng = max(c.high - c.low, 1e-9)
        return body, rng, c.high - max(c.close, c.open), min(c.close, c.open) - c.low

    b0, r0, u0, l0 = parts(c0)
    b1, r1, _, _ = parts(c1)
    b2, r2, _, _ = parts(c2)
    bull0, bull1, bull2 = c0.close > c0.open, c1.close > c1.open, c2.close > c2.open
    prior = df.close.iloc[-8:-1]
    down, up = prior.iloc[-1] < prior.iloc[0], prior.iloc[-1] > prior.iloc[0]

    if bull0 and not bull1 and c0.close >= c1.open and c0.open <= c1.close and b0 > b1:
        return 1.0, "Bullish engulfing"
    if (not bull0) and bull1 and c0.open >= c1.close and c0.close <= c1.open and b0 > b1:
        return -1.0, "Bearish engulfing"
    if (not bull2) and b2 / r2 > 0.5 and b1 / r1 < 0.3 and bull0 and c0.close > (c2.open + c2.close) / 2:
        return 1.0, "Morning star"
    if bull2 and b2 / r2 > 0.5 and b1 / r1 < 0.3 and (not bull0) and c0.close < (c2.open + c2.close) / 2:
        return -1.0, "Evening star"
    if l0 >= 0.6 * r0 and b0 <= 0.3 * r0 and u0 <= 0.15 * r0 and down:
        return 0.7, "Hammer after a decline"
    if u0 >= 0.6 * r0 and b0 <= 0.3 * r0 and l0 <= 0.15 * r0 and up:
        return -0.7, "Shooting star after a rise"
    if b0 <= 0.1 * r0:
        return 0.0, "Doji (indecision)"
    if b0 / r0 >= 0.7:
        return (0.4, "Strong bullish candle") if bull0 else (-0.4, "Strong bearish candle")
    return 0.0, "No clear pattern"


# --------------------------------------------------------------- scoring
def _sign(x: float) -> float:
    return float(np.sign(x))


def analyse(df: pd.DataFrame, interval: str, rules: dict, bench: pd.DataFrame | None = None) -> dict:
    if len(df) < 60:
        raise ValueError("Need at least 60 candles to calculate indicators")
    d = add_indicators(df, interval)
    last, prev = d.iloc[-1], d.iloc[-2]
    price, n = float(last.close), len(d)
    atr_v = float(last.atr)
    W = rules["weights"]
    factors = []

    def add(key, f, note):
        factors.append({
            "key": key, "factor": LABELS[key], "weight": W[key],
            "value": None if f is None else float(np.clip(f, -1, 1)), "note": note,
        })

    # 1. EMA trend
    conds = [price > last.ema20, last.ema20 > last.ema50, last.ema9 > last.ema20]
    if n >= 200:
        conds.append(price > last.ema200)
    add("ema_trend", 2 * float(np.mean(conds)) - 1,
        f"Close {'above' if price > last.ema20 else 'below'} EMA20; "
        f"EMA20 {'above' if last.ema20 > last.ema50 else 'below'} EMA50")

    # 2. RSI
    r = float(last.rsi)
    f = float(np.clip((r - 50) / 15, -1, 1))
    note = f"RSI {r:.0f}"
    if r > 75:
        f, note = 0.3, f"RSI {r:.0f} (overbought, momentum may be stretched)"
    elif r < 25:
        f, note = -0.3, f"RSI {r:.0f} (oversold, selling may be stretched)"
    add("rsi", f, note)

    # 3. MACD
    h, hp = float(last.macd_hist), float(prev.macd_hist)
    add("macd", 0.6 * _sign(h) + 0.4 * _sign(h - hp),
        f"Histogram {'positive' if h > 0 else 'negative'} and {'rising' if h > hp else 'falling'}")

    # 4. VWAP
    if pd.isna(last.vwap):
        add("vwap", None, "n/a (daily bars or no volume data)")
    else:
        add("vwap", 1.0 if price > last.vwap else -1.0,
            f"Price {'above' if price > last.vwap else 'below'} VWAP {last.vwap:,.2f}")

    # 5. Candlestick
    cf, cname = candle_pattern(d)
    add("candle", cf, cname)

    # 6. Volume confirmation
    if pd.isna(last.volma) or last.volma == 0 or last.volume == 0:
        add("volume", None, "n/a (no volume data)")
    else:
        rel = float(last.volume / last.volma)
        direction = _sign(last.close - last.open)
        add("volume", direction * min(rel / 1.5, 1.0) if rel >= 0.8 else 0.0,
            f"Volume {rel:.1f}x its 20-bar average on a {'green' if direction > 0 else 'red' if direction < 0 else 'flat'} candle")

    # 7. Support / resistance
    sup, res = swing_levels(d)
    below = [s for s in sup if s < price]
    above = [x for x in res if x > price]
    s_lvl = max(below) if below else None
    r_lvl = min(above) if above else None
    if s_lvl is not None and r_lvl is not None:
        ds, dr = price - s_lvl, r_lvl - price
        add("support_resistance", (dr - ds) / (dr + ds),
            f"Support {s_lvl:,.2f}, resistance {r_lvl:,.2f} (closer to support favours buyers)")
    elif s_lvl is not None:
        add("support_resistance", 0.3, f"Support {s_lvl:,.2f}, no resistance overhead (price at highs)")
    elif r_lvl is not None:
        add("support_resistance", -0.3, f"Resistance {r_lvl:,.2f}, no support below (price at lows)")
    else:
        add("support_resistance", None, "No swing levels found")

    # 8. Market trend from benchmark (daily)
    if bench is not None and len(bench) >= 55:
        b = bench.close
        e20, e50 = ema(b, 20).iloc[-1], ema(b, 50).iloc[-1]
        bf = (_sign(b.iloc[-1] - e50) + _sign(e20 - e50)) / 2
        add("market_trend", bf, f"Benchmark {'above' if b.iloc[-1] > e50 else 'below'} its 50-EMA; "
                                f"20-EMA {'above' if e20 > e50 else 'below'} 50-EMA")
    else:
        add("market_trend", None, "n/a (no benchmark)")

    avail = [x for x in factors if x["value"] is not None and x["weight"] > 0]
    tot = float(sum(x["weight"] for x in avail))
    if tot == 0:
        raise ValueError("No usable factors (check weights and data)")
    net = 100 * sum(x["weight"] * x["value"] for x in avail) / tot
    bull_share = sum(x["weight"] * max(x["value"], 0) for x in avail) / tot
    bear_share = sum(x["weight"] * max(-x["value"], 0) for x in avail) / tot

    adx_v = float(last.adx) if not pd.isna(last.adx) else 0.0
    regime = "Trending" if adx_v >= 25 else "Weak trend" if adx_v >= 20 else "Choppy / ranging"

    reasons = []
    if rules["block_choppy"] and adx_v < rules["adx_min"]:
        action = "NO TRADE"
        reasons.append(f"Market is choppy (ADX {adx_v:.0f} < {rules['adx_min']}); trend signals are unreliable.")
    elif min(bull_share, bear_share) >= rules["conflict_ratio"]:
        action = "NO TRADE"
        reasons.append(f"Signals conflict: {bull_share:.0%} of the weight is bullish and {bear_share:.0%} bearish.")
    elif net >= rules["buy_threshold"]:
        action = "BUY"
    elif net <= rules["sell_threshold"]:
        action = "SELL"
    else:
        action = "WAIT"
        reasons.append(f"Score {net:+.0f} is between your sell ({rules['sell_threshold']}) and buy "
                       f"({rules['buy_threshold']}) thresholds.")
    for x in sorted(avail, key=lambda z: -abs(z["weight"] * z["value"]))[:4]:
        reasons.append(f"{x['factor']}: {x['note']}")

    plan = trade_plan(action, price, atr_v, rules)
    return {
        "action": action, "score": float(net), "regime": regime, "adx": adx_v,
        "bull_share": float(bull_share), "bear_share": float(bear_share),
        "price": price, "atr": atr_v, "time": d.index[-1], "factors": factors,
        "reasons": reasons, "plan": plan, "data": d,
        "support": s_lvl, "resistance": r_lvl,
    }


# ------------------------------------------------------------------ risk
def trade_plan(action: str, price: float, atr_v: float, rules: dict):
    if action not in ("BUY", "SELL") or not atr_v or math.isnan(atr_v):
        return None
    d = 1 if action == "BUY" else -1
    risk = atr_v * rules["atr_sl_mult"]
    if risk <= 0:
        return None
    lot = max(rules["lot_size"], 1e-9)
    max_loss = rules["capital"] * rules["risk_pct"] / 100
    qty_risk = math.floor(max_loss / (risk * lot))
    qty_cap = math.floor(rules["capital"] * rules["leverage"] / (price * lot))
    qty = max(0, min(qty_risk, qty_cap))
    return {
        "entry": price,
        "stop": price - d * risk,
        "target1": price + d * risk * rules["rr1"],
        "target2": price + d * risk * rules["rr2"],
        "rr1": rules["rr1"], "rr2": rules["rr2"],
        "qty_lots": qty, "units": qty * lot,
        "capital_at_risk": qty * lot * risk,
        "position_value": qty * lot * price,
        "limited_by": "capital/leverage" if qty_cap < qty_risk else "risk per trade",
    }


# --------------------------------------------------------------- logging
def log_signal(symbol: str, interval: str, res: dict) -> None:
    row = {
        "logged_at": pd.Timestamp.now(tz=TZ).isoformat(timespec="seconds"),
        "candle_time": str(res["time"]), "symbol": symbol, "interval": interval,
        "action": res["action"], "score": round(res["score"], 1),
        "regime": res["regime"], "price": round(res["price"], 4),
    }
    pd.DataFrame([row]).to_csv(SIGNAL_LOG, mode="a", header=not os.path.exists(SIGNAL_LOG), index=False)


JOURNAL_COLS = ["date", "time", "symbol", "side", "qty", "entry", "exit", "pnl", "note"]


def read_journal() -> pd.DataFrame:
    if os.path.exists(JOURNAL_FILE):
        return pd.read_csv(JOURNAL_FILE)
    return pd.DataFrame(columns=JOURNAL_COLS)


def add_trade(symbol: str, side: str, qty: float, entry: float, exit_: float, note: str = "") -> None:
    now = pd.Timestamp.now(tz=TZ)
    pnl = (exit_ - entry) * qty * (1 if side == "BUY" else -1)
    row = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "symbol": symbol,
           "side": side, "qty": qty, "entry": entry, "exit": exit_, "pnl": round(pnl, 2), "note": note}
    pd.DataFrame([row]).to_csv(JOURNAL_FILE, mode="a", header=not os.path.exists(JOURNAL_FILE), index=False)


def lockout_status(rules: dict) -> dict:
    j = read_journal()
    today = pd.Timestamp.now(tz=TZ).strftime("%Y-%m-%d")
    t = j[j["date"].astype(str) == today] if len(j) else j
    pnl = float(t["pnl"].sum()) if len(t) else 0.0
    limit = rules["capital"] * rules["daily_loss_limit_pct"] / 100
    reasons = []
    if pnl <= -limit:
        reasons.append(f"Daily loss limit reached (P&L {pnl:,.0f}, limit -{limit:,.0f})")
    if len(t) >= rules["max_trades_per_day"]:
        reasons.append(f"Max trades per day reached ({len(t)}/{rules['max_trades_per_day']})")
    return {"locked": bool(reasons), "reasons": reasons, "pnl": pnl, "trades": len(t), "limit": limit}
