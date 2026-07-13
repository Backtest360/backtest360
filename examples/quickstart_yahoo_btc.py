"""Quickstart — RSI threshold long-only strategy on BTC daily data.

Demonstrates: built-in template, minimal setup, equity plot.

Requirements (beyond backtest360):
    pip install yfinance matplotlib
"""

import os

import matplotlib.pyplot as plt

from backtest360 import Client, Strategy

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download data — fall back to the engine's built-in sample dataset if Yahoo
# is unavailable, so the example runs without an external data feed.
# ---------------------------------------------------------------------------

try:
    import yfinance as yf

    df = yf.download(
        "BTC-USD",
        period="1y",
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
# Run backtest
# ---------------------------------------------------------------------------

result = client.backtest(Strategy.rsi_threshold_long(), df)

# ---------------------------------------------------------------------------
# Inspect results
# ---------------------------------------------------------------------------

result.summary()
mdd = result.stats.get("max_drawdown")
print(f"Max Drawdown: {mdd:.1%}" if mdd is not None else "Max Drawdown: n/a")
print(f"Trades: {len(result.trades)}")

result.strategy_equity.plot(title="BTC RSI threshold — equity curve")
plt.tight_layout()
plt.show()
