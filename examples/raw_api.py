"""Raw API escape hatch — backtest_raw() for full wire-level control.

Use when you want to build the exact JSON payload yourself with the API
docs open, bypassing the client's helper classes entirely.

API reference: https://api.backtest360.com/docs

Requirements (beyond backtest360):
    pip install yfinance
"""

import os

from backtest360 import Client

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Download and serialise data manually — fall back to the engine's built-in
# sample dataset if Yahoo is unavailable, so the example runs without an
# external data feed.
# ---------------------------------------------------------------------------

try:
    import yfinance as yf

    df = yf.download(
        "BTC-USD", period="1y", interval="1d",
        auto_adjust=False, multi_level_index=False, progress=False,
    )
    df.columns = df.columns.str.lower()
    if df.empty:
        raise RuntimeError("no data returned")
except Exception:
    print("Yahoo data unavailable — using the engine's built-in BTC sample dataset.")
    df = client.sample_data("BTC")

ohlcv = {
    "dates": [ts.isoformat() for ts in df.index],
    "open":  df["open"].tolist(),
    "high":  df["high"].tolist(),
    "low":   df["low"].tolist(),
    "close": df["close"].tolist(),
    "volume": df["volume"].tolist(),
}

# ---------------------------------------------------------------------------
# Build the raw payload — the leg-based {"run": {...}, "legs": [...]} envelope
# that /api/backtest expects. Each leg carries its own data_source, strategy,
# and execution; a benchmark would be a separate {"benchmark": true} leg.
# ---------------------------------------------------------------------------

payload = {
    "run": {
        # backtest_raw() sends the payload verbatim — it does not default this
        # field the way backtest()/backtest_signals() do. "ids" keys the
        # returned stats dict by stable snake_case metric id (e.g. "sharpe")
        # instead of the engine's default display label ("Sharpe"). See the
        # metrics catalog on GET /api/sections for the full mapping.
        "stats_keys": "ids",
    },
    "legs": [
        {
            "id": "strategy",
            "data_source": {
                "ohlcv": ohlcv,
            },
            "strategy": {
                "condition_tree": {
                    "long_entry":  {"op": "leaf", "expr": "rsi_14 < 30"},
                    "long_exit":   {"op": "leaf", "expr": "rsi_14 > 70"},
                    "short_entry": None,
                    "short_exit":  None,
                },
                "indicators": [
                    {
                        "ref":      "rsi_14",
                        "name":     "rsi",
                        "kind":     "technical",
                        "params":   {"period": 14},
                        "upstream": [],
                    },
                ],
            },
            # These flat keys mirror the wire form of the Execution and Costs
            # classes. The Execution(...)/Costs(...) helpers also emit their
            # defaults (entry_window/exit_window, entry_fill/exit_fill,
            # vol_scaled_slippage, vol_slippage_lookback), so their output is a
            # superset of the keys below.
            "execution": {
                "signal_frequency": "daily",
                "entry_anchor":     "open",
                "exit_anchor":      "close",
                "slippage_bps":     2.5,
                "fee_pct":          0.001,
            },
        },
    ],
}

# ---------------------------------------------------------------------------
# Send and inspect the raw response
# ---------------------------------------------------------------------------

resp = client.backtest_raw(payload)

# resp is the full engine response dict — {"status", "run", "legs": [...]}.
# Each leg carries its own "result"; pull the strategy leg's (id "strategy").
result = resp["legs"][0]["result"]
print("Sharpe:", result.get("stats", {}).get("sharpe"))
print("Keys in result:", list(result.keys()))
