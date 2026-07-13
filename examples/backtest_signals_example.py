"""backtest_signals — backtest a pre-computed signal series.

Demonstrates: signal logic that lives OUTSIDE the engine. Here a simple moving-average
crossover is computed in pandas and handed to the engine, which runs execution, costing,
and statistics directly on your series — no engine-side signal generation.

Requirements (beyond backtest360):
    pip install yfinance
"""

import os

from backtest360 import Client

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download data — fall back to the engine's built-in sample dataset if Yahoo
# is unavailable, so the example runs without an external data feed.
# ---------------------------------------------------------------------------

try:
    import yfinance as yf

    df = yf.download(
        "BTC-USD",
        period="2y",
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
# Compute a signal series yourself (any external logic works)
# ---------------------------------------------------------------------------
# Long (1) while the 20-day SMA is above the 50-day SMA, flat (0) otherwise.
# Values must be in {-1, 0, 1}, indexed by datetime, aligned to the OHLCV frame.

fast = df["close"].rolling(20).mean()
slow = df["close"].rolling(50).mean()
signals = (fast > slow).astype(int)  # True -> 1, False -> 0 (flat during warm-up)

# ---------------------------------------------------------------------------
# Backtest the signal series
# ---------------------------------------------------------------------------

result = client.backtest_signals(signals, df, name="sma_20_50_crossover")

# ---------------------------------------------------------------------------
# Inspect results
# ---------------------------------------------------------------------------

result.summary()
print(f"Trades: {len(result.trades)}")
