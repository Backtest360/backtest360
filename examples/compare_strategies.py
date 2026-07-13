"""Compare two strategies side-by-side on the same data.

Runs two backtests and plots both equity curves for visual comparison.

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
        "SPY", start="2018-01-01", end="2024-01-01", interval="1d",
        auto_adjust=False, multi_level_index=False, progress=False,
    )
    df.columns = df.columns.str.lower()
    if df.empty:
        raise RuntimeError("no data returned")
except Exception:
    print("Yahoo data unavailable — using the engine's built-in SPY sample dataset.")
    df = client.sample_data("SPY")

result_rsi = client.backtest(Strategy.rsi_mean_reversion(), df)
result_mom = client.backtest(Strategy.momentum_6m_long(), df)

# ---------------------------------------------------------------------------

fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

result_rsi.strategy_equity.plot(ax=axes[0], title="RSI Mean Reversion", color="steelblue")
result_mom.strategy_equity.plot(ax=axes[1], title="6-Month Momentum", color="darkorange")

for ax in axes:
    ax.set_ylabel("Equity")
    ax.grid(alpha=0.3)

print("RSI Sharpe:", result_rsi.stats.get("sharpe"),
      "  Momentum Sharpe:", result_mom.stats.get("sharpe"))

plt.tight_layout()
plt.show()
