"""Custom strategy — grouped-knob classes (Execution / Costs / Risk / Sizing).

Demonstrates: building a custom strategy with expression strings and
indicator references, then running it with explicit execution config.

Full indicator library (names, params, output columns):
    https://api.backtest360.com/docs

Requirements (beyond backtest360):
    pip install yfinance
"""

import os

from backtest360 import Client, Costs, Execution, Risk, Sizing, Strategy

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download data — fall back to the engine's built-in sample datasets if Yahoo
# is unavailable, so the example runs without an external data feed.
# ---------------------------------------------------------------------------

# Traded asset: a single name (AAPL).  Benchmark: the broad market (SPY).
try:
    import yfinance as yf

    def _download(ticker):
        d = yf.download(
            ticker,
            start="2018-01-01",
            end="2024-01-01",
            interval="1d",
            auto_adjust=False,
            multi_level_index=False,
            progress=False,
        )
        d.columns = d.columns.str.lower()
        if d.empty:
            raise RuntimeError("no data returned")
        return d

    df = _download("AAPL")
    spy_df = _download("SPY")
except Exception:
    print("Yahoo data unavailable — using the engine's built-in sample datasets "
          "(QQQ traded, SPY benchmark).")
    df = client.sample_data("QQQ")
    spy_df = client.sample_data("SPY")

# ---------------------------------------------------------------------------
# Build strategy
# ---------------------------------------------------------------------------

# "rsi" is the ref — use it in expressions.  Strategy.indicator() defaults
# ref to the indicator name, so "rsi" in the expression matches the indicator.
# Mean-reversion logic: enter on recovery from oversold (30 < rsi < 50),
# exit on return to neutral (rsi >= 50).
# For disambiguation (two RSIs at different periods), pass ref= explicitly:
#   Strategy.indicator("rsi", ref="rsi_fast", period=5)
#   Strategy.indicator("rsi", ref="rsi_slow", period=20)

strat = Strategy(
    name="rsi_mean_reversion",
    long_entry="(rsi > 30) & (rsi < 50)",
    long_exit="rsi >= 50",
    indicators=[Strategy.indicator("rsi", period=14)],
)

# ---------------------------------------------------------------------------
# Run backtest with all knobs
# ---------------------------------------------------------------------------

result = client.backtest(
    strat,
    df,
    benchmark=spy_df,
    execution=Execution(entry="open", exit="close", signal_frequency="daily"),
    costs=Costs(slippage_bps=2.5, fee_pct=0.001),
    risk=Risk(stop="trailing_atr", value=2.5, atr_period=14, max_drawdown=0.25),
    sizing=Sizing(weight=1.0, vol_target=0.15, leverage_limit=2.0),
)

# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------

print("Sharpe:", result.stats.get("sharpe"))
mdd = result.stats.get("max_drawdown")
print(f"Max Drawdown: {mdd:.1%}" if mdd is not None else "Max Drawdown: n/a")

for t in result.trades[:5]:
    print(t["entry_date"], t["direction"], t["return_net"])
