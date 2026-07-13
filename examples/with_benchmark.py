"""Benchmark comparison — alpha, beta, up/down capture.

Pass benchmark=spy_df to get benchmark-relative metrics in result.stats.
Metrics added when a benchmark is present: Alpha, Beta, Information Ratio,
Tracking Error, Up Capture, Down Capture.

Requirements (beyond backtest360):
    pip install yfinance matplotlib
"""

import os

import matplotlib.pyplot as plt

from backtest360 import Client, Strategy

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download a traded asset + a benchmark — fall back to the engine's built-in
# sample datasets if Yahoo is unavailable, so the example runs without an
# external data feed.
# ---------------------------------------------------------------------------

try:
    import yfinance as yf

    def _download(ticker):
        df = yf.download(
            ticker, start="2020-01-01", end="2024-01-01", interval="1d",
            auto_adjust=False, multi_level_index=False, progress=False,
        )
        df.columns = df.columns.str.lower()
        if df.empty:
            raise RuntimeError("no data returned")
        return df

    df = _download("AAPL")
    benchmark = _download("SPY")
except Exception:
    print("Yahoo data unavailable — using the engine's built-in sample datasets "
          "(QQQ traded, SPY benchmark).")
    df = client.sample_data("QQQ")
    benchmark = client.sample_data("SPY")

result = client.backtest(Strategy.rsi_mean_reversion(), df, benchmark=benchmark)

print("Sharpe:", result.stats.get("sharpe"))
print("Alpha:", result.stats.get("alpha"))
print("Beta:", result.stats.get("beta"))
print("Up Capture:", result.stats.get("up_capture"))
print("Down Capture:", result.stats.get("down_capture"))

ax = result.strategy_equity.plot(label="AAPL Strategy")
result.benchmark_equity.plot(ax=ax, label="SPY (buy & hold)", linestyle="--")
ax.legend()
plt.tight_layout()
plt.show()
