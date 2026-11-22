"""My Trading Workbench: free dashboard. Run with:  streamlit run app.py"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import engine as E

st.set_page_config(page_title="My Trading Workbench", layout="wide")

PRESETS = {
    "India stocks (NSE)": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "SBIN.NS", "ITC.NS", "LT.NS"],
    "India indices": ["^NSEI", "^NSEBANK", "^BSESN"],
    "Forex": ["USDINR=X", "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X"],
    "Gold, silver, crude (futures)": ["GC=F", "SI=F", "CL=F"],
    "US stocks": ["AAPL", "MSFT", "NVDA", "TSLA"],
    "Crypto": ["BTC-USD", "ETH-USD"],
}
DEFAULT_BENCH = {"India stocks (NSE)": "^NSEI", "US stocks": "^GSPC", "Crypto": "BTC-USD"}
COLORS = {"BUY": "green", "SELL": "red", "WAIT": "orange", "NO TRADE": "gray"}
INTERVALS = list(E.INTERVAL_MIN)


@st.cache_data(ttl=20, show_spinner=False)
def load(symbol, interval, closed_only):
    return E.fetch(symbol, interval, closed_only)


@st.cache_data(ttl=300, show_spinner=False)
def load_bench(symbol):
    return E.fetch(symbol, "1d", False)


def get_bench(sym, own):
    if not sym or sym == own:
        return None
    try:
        return load_bench(sym)
    except Exception:
        return None


# ------------------------------------------------------------- sidebar
rules = E.load_rules()
with st.sidebar:
    st.header("Your rules")
    st.caption("Change anything here. Press Save to keep it (also used by monitor.py).")
    w = {}
    with st.expander("Factor weights"):
        for k, label in E.LABELS.items():
            w[k] = st.slider(label, 0, 30, int(rules["weights"][k]), key=f"w_{k}")
        st.caption(f"Total weight {sum(w.values())}. Scores are rescaled to -100..+100, "
                   "and factors with no data are skipped.")
    rules["weights"] = w
    rules["buy_threshold"] = st.slider("BUY when score is at least", 10, 90, int(rules["buy_threshold"]))
    rules["sell_threshold"] = -st.slider("SELL when score is at most minus", 10, 90, int(-rules["sell_threshold"]))
    rules["conflict_ratio"] = st.slider("NO TRADE if bull and bear share both exceed", 0.10, 0.60,
                                        float(rules["conflict_ratio"]), 0.05)
    rules["block_choppy"] = st.checkbox("NO TRADE when market is choppy (low ADX)", rules["block_choppy"])
    rules["adx_min"] = st.slider("Choppy below ADX", 10, 30, int(rules["adx_min"]))
    st.subheader("Risk")
    rules["capital"] = st.number_input("Capital", 1000.0, 1e9, float(rules["capital"]), 1000.0)
    rules["risk_pct"] = st.number_input("Risk per trade (% of capital)", 0.1, 10.0, float(rules["risk_pct"]), 0.1)
    rules["leverage"] = st.number_input("Max leverage / margin multiple", 1.0, 20.0, float(rules["leverage"]), 0.5)
    rules["lot_size"] = st.number_input("Lot size (1 for shares)", 1, 100000, int(rules["lot_size"]))
    rules["atr_sl_mult"] = st.number_input("Stop = ATR x", 0.5, 5.0, float(rules["atr_sl_mult"]), 0.1)
    rules["rr1"] = st.number_input("Target 1 risk:reward", 0.5, 5.0, float(rules["rr1"]), 0.1)
    rules["rr2"] = st.number_input("Target 2 risk:reward", 0.5, 8.0, float(rules["rr2"]), 0.1)
    rules["max_trades_per_day"] = st.number_input("Max trades per day", 1, 50, int(rules["max_trades_per_day"]))
    rules["daily_loss_limit_pct"] = st.number_input("Daily loss limit (% of capital)", 0.5, 20.0,
                                                    float(rules["daily_loss_limit_pct"]), 0.5)
    rules["closed_candles_only"] = st.checkbox("Use closed candles only (recommended)", rules["closed_candles_only"])
    if st.button("Save rules", type="primary"):
        E.save_rules(rules)
        st.success("Saved to rules.json")

st.title("My Trading Workbench")
st.caption("Decision support only. It does not place orders. Free Yahoo Finance data can be delayed "
           "and occasionally wrong, so confirm price on your broker before acting.")

tab_a, tab_s, tab_j, tab_h = st.tabs(["Analyse", "Scanner", "Journal", "How it works"])

# -------------------------------------------------------------- analyse
with tab_a:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1.3])
    market = c1.selectbox("Market", list(PRESETS))
    pick = c2.selectbox("Symbol", PRESETS[market])
    custom = c3.text_input("Or any Yahoo Finance symbol", "", placeholder="e.g. TATAMOTORS.NS")
    interval = c4.selectbox("Timeframe", INTERVALS, index=3)
    symbol = custom.strip() or pick
    d1, d2, d3 = st.columns([2, 1, 1])
    bench_sym = d1.text_input("Market-trend benchmark (blank to skip)", DEFAULT_BENCH.get(market, ""),
                              key=f"bench_{market}")
    live = d2.toggle("Live refresh", value=False)
    every = d3.number_input("Refresh every (sec)", 30, 600, 60)

    def panel():
        lock = E.lockout_status(rules)
        if lock["locked"]:
            st.error("LOCKED OUT for today: " + "; ".join(lock["reasons"]))
        try:
            df = load(symbol, interval, rules["closed_candles_only"] and interval != "1d")
            res = E.analyse(df, interval, rules, get_bench(bench_sym, symbol))
        except Exception as e:
            st.error(f"Could not analyse {symbol}: {e}")
            return

        key = (symbol, interval)
        logged = st.session_state.setdefault("logged", {})
        if logged.get(key) != (str(res["time"]), res["action"]):
            logged[key] = (str(res["time"]), res["action"])
            E.log_signal(symbol, interval, res)

        age = pd.Timestamp.now(tz=E.TZ) - res["time"]
        if age > pd.Timedelta(minutes=E.INTERVAL_MIN[interval] * 3):
            st.warning(f"Last candle is {age.total_seconds() / 3600:.1f} hours old: market may be closed or data delayed.")

        st.header(f":{COLORS[res['action']]}[{res['action']}]  {symbol}")
        m = st.columns(5)
        m[0].metric("Score (-100 to +100)", f"{res['score']:+.0f}")
        m[1].metric("Regime", res["regime"], f"ADX {res['adx']:.0f}", delta_color="off")
        m[2].metric("Last close", f"{res['price']:,.2f}")
        m[3].metric("ATR", f"{res['atr']:,.2f}")
        m[4].metric("Candle time", f"{res['time']:%d %b %H:%M}")

        p = res["plan"]
        if p:
            t = st.columns(6)
            t[0].metric("Entry", f"{p['entry']:,.2f}")
            t[1].metric("Stop-loss", f"{p['stop']:,.2f}")
            t[2].metric(f"Target 1 ({p['rr1']}R)", f"{p['target1']:,.2f}")
            t[3].metric(f"Target 2 ({p['rr2']}R)", f"{p['target2']:,.2f}")
            t[4].metric("Quantity (lots)", f"{p['qty_lots']}")
            t[5].metric("Capital at risk", f"{p['capital_at_risk']:,.0f}")
            if p["qty_lots"] == 0:
                st.warning("Quantity is 0: the stop is too wide for your risk or capital settings.")
            st.caption(f"Position value {p['position_value']:,.0f}; size limited by {p['limited_by']}. "
                       "Brokerage, taxes and slippage are not included.")

        st.subheader("Why")
        for r_ in res["reasons"]:
            st.write("- " + r_)

        tbl = pd.DataFrame([{"Factor": f["factor"], "Weight": f["weight"],
                             "Reading (-1 bearish, +1 bullish)": None if f["value"] is None else round(f["value"], 2),
                             "Detail": f["note"]} for f in res["factors"]])
        st.dataframe(tbl, hide_index=True)

        v = res["data"].iloc[-150:]
        x = v.index.strftime("%d %b %H:%M")
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(x=x, open=v.open, high=v.high, low=v.low, close=v.close, name="Price"), 1, 1)
        for col, nm in (("ema20", "EMA20"), ("ema50", "EMA50"), ("vwap", "VWAP")):
            fig.add_trace(go.Scatter(x=x, y=v[col], name=nm, mode="lines", line=dict(width=1.2)), 1, 1)
        if p:
            for lvl, nm in ((p["stop"], "Stop"), (p["target1"], "T1"), (p["target2"], "T2")):
                fig.add_hline(y=lvl, line_dash="dash", annotation_text=nm, row=1, col=1)
        fig.add_trace(go.Bar(x=x, y=v.volume, name="Volume"), 2, 1)
        fig.add_trace(go.Scatter(x=x, y=v.rsi, name="RSI", mode="lines"), 3, 1)
        fig.add_hline(y=70, line_dash="dot", row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", row=3, col=1)
        fig.update_xaxes(type="category", nticks=10, rangeslider_visible=False)
        fig.update_layout(height=700, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig)

    st.fragment(panel, run_every=int(every) if live else None)()

# -------------------------------------------------------------- scanner
with tab_s:
    st.write("Scan a whole list at once and see which symbols currently pass your rules.")
    s1, s2 = st.columns([2, 1])
    mk = s1.selectbox("List", list(PRESETS), key="scan_mk")
    scan_int = s2.selectbox("Timeframe", INTERVALS, index=3, key="scan_int")
    chosen = st.multiselect("Symbols", PRESETS[mk], default=PRESETS[mk], key=f"scan_sel_{mk}")
    extra = st.text_input("Extra symbols (comma separated)", key="scan_extra")
    syms = chosen + [s.strip() for s in extra.split(",") if s.strip()]
    b1, b2 = st.columns(2)
    if b1.button("Scan now", type="primary"):
        bench = get_bench(DEFAULT_BENCH.get(mk, ""), "")
        rows, prog = [], st.progress(0.0)
        for i, s in enumerate(syms):
            try:
                df = load(s, scan_int, rules["closed_candles_only"] and scan_int != "1d")
                r = E.analyse(df, scan_int, rules, bench)
                p = r["plan"]
                rows.append({"Symbol": s, "Action": r["action"], "Score": round(r["score"]), "Regime": r["regime"],
                             "Price": round(r["price"], 2),
                             "Stop": round(p["stop"], 2) if p else None,
                             "Target 1": round(p["target1"], 2) if p else None,
                             "Qty": p["qty_lots"] if p else None})
            except Exception as e:
                rows.append({"Symbol": s, "Action": "ERROR", "Regime": str(e)[:60]})
            prog.progress((i + 1) / max(len(syms), 1))
        st.session_state["scan_rows"] = rows
    if "scan_rows" in st.session_state:
        st.dataframe(pd.DataFrame(st.session_state["scan_rows"]), hide_index=True)
    if b2.button("Use this list for monitor.py"):
        rules["watchlist"], rules["interval"], rules["benchmark"] = syms, scan_int, DEFAULT_BENCH.get(mk, "")
        E.save_rules(rules)
        st.success("Saved. monitor.py will use this watchlist.")

# -------------------------------------------------------------- journal
with tab_j:
    lock = E.lockout_status(rules)
    k = st.columns(3)
    k[0].metric("Today's P&L", f"{lock['pnl']:,.0f}", f"limit -{lock['limit']:,.0f}", delta_color="off")
    k[1].metric("Trades today", f"{lock['trades']} / {rules['max_trades_per_day']}")
    k[2].metric("Status", "LOCKED OUT" if lock["locked"] else "OK to trade")
    with st.form("trade", clear_on_submit=True):
        f1, f2, f3, f4, f5 = st.columns(5)
        t_sym = f1.text_input("Symbol")
        t_side = f2.selectbox("Side", ["BUY", "SELL"])
        t_qty = f3.number_input("Quantity (units)", 0.0, 1e9, 1.0)
        t_in = f4.number_input("Entry price", 0.0, 1e9, 0.0, format="%.4f")
        t_out = f5.number_input("Exit price", 0.0, 1e9, 0.0, format="%.4f")
        t_note = st.text_input("Note (setup, mistake, mood)")
        if st.form_submit_button("Add closed trade") and t_sym and t_in > 0 and t_out > 0:
            E.add_trade(t_sym.strip(), t_side, t_qty, t_in, t_out, t_note)
            st.rerun()
    j = E.read_journal()
    if len(j):
        wins, losses = j[j.pnl > 0].pnl, j[j.pnl <= 0].pnl
        s = st.columns(4)
        s[0].metric("Trades", len(j))
        s[1].metric("Win rate", f"{len(wins) / len(j):.0%}")
        s[2].metric("Total P&L", f"{j.pnl.sum():,.0f}")
        s[3].metric("Expectancy / trade", f"{j.pnl.mean():,.0f}")
        st.dataframe(j.iloc[::-1], hide_index=True)
    else:
        st.info("No trades logged yet. Logging every trade, including the bad ones, is what shows you what works.")

# ----------------------------------------------------------------- help
with tab_h:
    st.markdown("""
**What it does:** pulls free candle data, calculates indicators, scores 8 factors with your weights,
and returns BUY, SELL, WAIT or NO TRADE with the reasons. For BUY/SELL it adds ATR-based stop, two targets and a
position size from your capital and risk %.

**NO TRADE** appears when the market is choppy (low ADX) or the factors disagree strongly. Skipping trades is
part of the edge.

**The score is not a probability.** The weights are starting assumptions. Use `signals_log.csv` and the journal
to see whether signals have actually worked before trusting them with real money.

**Not included yet:** options chains, Indian futures data, brokerage and tax costs, backtesting, order placement.
""")
