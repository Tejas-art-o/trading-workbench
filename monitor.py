"""Headless monitor: runs in a terminal, watches your watchlist, alerts on new BUY/SELL signals.

Free alerts: set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID (see README). Without them it prints to the console.
Run:  python monitor.py
"""
import os
import time

import requests

import engine as E


def send(msg: str) -> None:
    print(msg, flush=True)
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": msg}, timeout=15)
        except Exception as e:  # never crash the loop over an alert
            print("Telegram send failed:", e, flush=True)


def fmt(symbol: str, interval: str, r: dict) -> str:
    lines = [f"{r['action']}  {symbol} ({interval})",
             f"Score {r['score']:+.0f} | {r['regime']} | price {r['price']:,.2f} | candle {r['time']:%d-%b %H:%M}"]
    p = r["plan"]
    if p:
        lines.append(f"Entry {p['entry']:,.2f}  SL {p['stop']:,.2f}  T1 {p['target1']:,.2f}  T2 {p['target2']:,.2f}")
        lines.append(f"Qty {p['qty_lots']} | capital at risk {p['capital_at_risk']:,.0f}")
    lines.append("Decision support only. Verify on your broker before trading.")
    return "\n".join(lines)


def main() -> None:
    seen = {}
    print("Monitor started. Ctrl+C to stop.", flush=True)
    while True:
        rules = E.load_rules()  # reloaded every loop so saved rule changes apply
        interval = rules["interval"]
        bench = None
        if rules.get("benchmark"):
            try:
                bench = E.fetch(rules["benchmark"], "1d", False)
            except Exception as e:
                print("Benchmark unavailable:", e, flush=True)
        for sym in rules["watchlist"]:
            try:
                df = E.fetch(sym, interval, rules["closed_candles_only"] and interval != "1d")
                r = E.analyse(df, interval, rules, bench if sym != rules.get("benchmark") else None)
            except Exception as e:
                print(f"{sym}: {e}", flush=True)
                continue
            key = (str(r["time"]), r["action"])
            if seen.get(sym) == key:
                continue
            seen[sym] = key
            E.log_signal(sym, interval, r)
            if r["action"] in ("BUY", "SELL"):
                lock = E.lockout_status(rules)
                if lock["locked"]:
                    send(f"{sym}: {r['action']} signal suppressed. " + "; ".join(lock["reasons"]))
                else:
                    send(fmt(sym, interval, r))
            else:
                print(f"{sym}: {r['action']} (score {r['score']:+.0f}, {r['regime']})", flush=True)
        time.sleep(max(30, int(rules["poll_seconds"])))  # be polite to the free data source


if __name__ == "__main__":
    main()
