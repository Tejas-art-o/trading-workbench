# My Trading Workbench (free)

A free decision-support tool: live-ish candle data, indicators, your own rules, and BUY / SELL / WAIT / NO TRADE
signals with stop-loss, targets and position size. It never places orders.

## Setup (once)
1. Install Python 3.10+ from python.org (free).
2. Open a terminal in this folder and run:  `pip install -r requirements.txt`

## Run the dashboard
`streamlit run app.py`  (opens in your browser)

- **Analyse**: pick a market and symbol, see the signal, reasons, plan and chart. Turn on "Live refresh".
- **Scanner**: scan a whole list and see which symbols pass your rules.
- **Journal**: log closed trades. Daily loss limit and max-trades lockout are enforced from here.
- Edit weights, thresholds and risk in the left sidebar, then press **Save rules**.

## Run alerts without the browser
`python monitor.py` watches your saved watchlist and prints new BUY/SELL signals.

Free phone alerts with Telegram:
1. In Telegram, message @BotFather, send `/newbot`, copy the token.
2. Message your new bot once, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your chat id.
3. Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` environment variables, then run monitor.py.

The monitor only runs while your computer (or a free/cheap server) is on.

## Symbols (Yahoo Finance format)
NSE stocks `RELIANCE.NS`, Nifty `^NSEI`, Bank Nifty `^NSEBANK`, USD/INR `USDINR=X`, EUR/USD `EURUSD=X`,
gold futures `GC=F`, crude `CL=F`. Any Yahoo symbol works in the custom box.

## Limits (read these)
- Yahoo data is free but can be delayed and occasionally wrong. Compare with your broker before acting.
- 1-minute data covers only the last few days; 5m about 30 days.
- No options chain, no Indian futures (NIFTY/BANKNIFTY futures), no backtester, no order placement yet.
  These need a broker API (Upstox, Angel One SmartAPI and Dhan offer free API access with an account) and are the next steps.
- Scores are not probabilities, and the default weights are untested. Paper trade first and review `signals_log.csv`.
- Scraping Groww or other apps is unreliable and usually breaks their terms of use, so this uses a data source meant for it.
- This is general information, not financial advice.
