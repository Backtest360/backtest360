# Examples

Runnable scripts demonstrating the Backtest360 Python client.

Each script is self-contained. Install requirements:

```bash
pip install backtest360 yfinance matplotlib
```

Set your API key:

```bash
export BACKTEST360_API_KEY=b360_...
```

| Script | What it demonstrates |
|---|---|
| `quickstart_yahoo_btc.py` | Quickstart — built-in template, BTC daily data |
| `backtest_signals_example.py` | `backtest_signals()` — backtest a pre-computed signal series (signal logic outside the engine) |
| `custom_strategy.py` | Custom RSI strategy with Execution / Costs / Risk / Sizing |
| `raw_api.py` | `backtest_raw()` escape hatch — full control over the wire payload |
| `with_benchmark.py` | Pass a benchmark DataFrame, read alpha / beta / capture |
| `latest_signal_live.py` | `latest_signal()` — the live-trading hook for the most-recent bar |
| `introspection.py` | `version()`, `list_indicators()`, `validate_strategy()` |
| `error_handling.py` | Catch `Backtest360Error`, branch on `.status` |
| `compare_strategies.py` | Run two strategies on the same data, plot both equity curves |
