"""Latest signal — the live-trading hook.

Demonstrates: building a strategy, loading recent OHLCV data, and calling
client.latest_signal() to ask "given everything up to the most recent bar,
what position should I be holding right now?"

latest_signal() returns only the most-recent bar's signal and per-condition
diagnostics — no P&L, no statistics. Run it on a schedule (e.g. once per
bar) to drive a live or paper-trading position.

Requirements (beyond backtest360):
    pip install yfinance
"""

import os

from backtest360 import Client, Strategy

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download recent data — fall back to the engine's built-in sample dataset if
# Yahoo is unavailable, so the example runs without an external data feed.
# ---------------------------------------------------------------------------

# Pull enough history to cover the strategy's indicator warmup — the engine
# needs lookback bars before it can evaluate the most-recent signal.
try:
    import yfinance as yf

    df = yf.download(
        "BTC-USD",
        period="6mo",
        interval="1d",
        auto_adjust=False,
        multi_level_index=False,
        progress=False,
    )
    df.columns = df.columns.str.lower()
    if df.empty:
        raise RuntimeError("no data returned")
except Exception:
    print("Yahoo data unavailable — using the engine's built-in BTC sample dataset.")
    df = client.sample_data("BTC")

# ---------------------------------------------------------------------------
# Ask the engine for the latest signal
# ---------------------------------------------------------------------------

signal = client.latest_signal(Strategy.rsi_threshold_long(), df)

# ---------------------------------------------------------------------------
# Interpret the result
# ---------------------------------------------------------------------------

# signal["signal"] is the target position for the most-recent bar:
#   1  -> be long
#   0  -> be flat (no position)
#  -1  -> be short
position = {1: "LONG", 0: "FLAT", -1: "SHORT"}.get(signal.get("signal"), "?")

print(f"As of the latest bar ({df.index[-1].date()}):")
print(f"  Target position: {position}  (signal = {signal.get('signal')})")
print(f"  Long entry fired this bar: {signal.get('long_entry_fired')}")
