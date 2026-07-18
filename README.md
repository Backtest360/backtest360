# backtest360

[![PyPI version](https://img.shields.io/pypi/v/backtest360.svg)](https://pypi.org/project/backtest360/)
[![Python versions](https://img.shields.io/pypi/pyversions/backtest360.svg)](https://pypi.org/project/backtest360/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![tests](https://github.com/Backtest360/backtest360/actions/workflows/test.yml/badge.svg)](https://github.com/Backtest360/backtest360/actions/workflows/test.yml)

**Official Python client for the Backtest360 API.**

Run backtests, automate, integrate with your research or trading workflows.
The client handles authentication, serialization, and error mapping so you can
call a single method instead of building and parsing the raw HTTP requests
yourself.

```python
import matplotlib.pyplot as plt
import yfinance as yf
from backtest360 import Client, Strategy

df = yf.download("BTC-USD", period="1y", interval="1d",
                 auto_adjust=False, multi_level_index=False, progress=False)
df.columns = df.columns.str.lower()

result = Client(api_key="b360_...").backtest(Strategy.rsi_threshold_long(), df)
result.summary()
result.strategy_equity.plot(title="Equity curve")
plt.show()
```

Save this snippet as `quickstart.py`, drop in your API key, and run it with
`python quickstart.py` (install the extras if needed: `pip install matplotlib yfinance`).

---

## Install

```bash
pip install backtest360
```

Requires Python 3.9+. The only runtime dependencies are `httpx` and `pandas`.

<details>
<summary>Need to install Python first? (Windows / macOS / Linux / WSL)</summary>

### Windows (native)

1. Install Python from [python.org/downloads](https://www.python.org/downloads/) (check "Add to PATH"), or via winget:
   ```powershell
   winget install Python.Python.3.12
   ```
2. Open PowerShell and verify:
   ```powershell
   python --version
   ```
3. Install the client and yfinance for the quickstart:
   ```powershell
   pip install backtest360 yfinance
   ```
4. Run a script:
   ```powershell
   python quickstart.py
   ```

### Windows via WSL (recommended for quant work)

1. Install WSL:
   ```powershell
   wsl --install
   ```
   Restart, then open the Ubuntu terminal.
2. Install Python:
   ```bash
   sudo apt update && sudo apt install python3 python3-pip -y
   ```
3. Install the client:
   ```bash
   pip3 install backtest360 yfinance
   ```
4. Run a script:
   ```bash
   python3 quickstart.py
   ```

### macOS

```bash
# via Homebrew (recommended)
brew install python

# or download from python.org/downloads
```

Then:
```bash
pip3 install backtest360 yfinance
python3 quickstart.py
```

### Linux

```bash
sudo apt install python3 python3-pip -y   # Debian/Ubuntu
pip3 install backtest360 yfinance
python3 quickstart.py
```

</details>

## Get an API key

Sign up at [backtest360.com](https://backtest360.com) and copy your key.
Store it in the `BACKTEST360_API_KEY` environment variable or pass it directly:

```python
client = Client(api_key="b360_...")
# or: export BACKTEST360_API_KEY=b360_...
client = Client()
```

The engine URL can be overridden with the optional `BACKTEST360_ENGINE_URL`
environment variable, read when the `Client` is constructed. An explicit
`base_url=` argument takes precedence over the environment variable, which in
turn takes precedence over the default:

```bash
export BACKTEST360_ENGINE_URL=https://api.backtest360.com
```

---

## Features

- Synchronous client — straightforward to use in scripts, notebooks, and pipelines
- Hand-written wrapper over the public REST API — no generated code, no schema sync
- Built-in strategy templates (`Strategy.rsi_threshold_long()`, `Strategy.ma_crossover()`, …)
- Grouped-knob classes: `Execution`, `Costs`, `Risk`, `Sizing`, `MarketHours`, `Settings` — set only what you need
- Pre-computed signals path: `client.backtest_signals(series, df)` for model-generated signals
- Pandas-native — pass a DataFrame, get pandas Series back (`result.strategy_equity`, `result.returns`)
- Built-in sample data served by the engine — examples run without external data feeds (`client.sample_data("BTC")`)
- Chart-annotation markers and data-quality report on every result (`result.markers`, `result.data_quality`)
- Engine introspection: `client.version()` for version compatibility checks, `client.me()` for your key's scopes/limits/usage
- Strategy templates: `client.list_templates()` to discover ready-to-run predesigned strategies
- Raw-API escape hatch for full control (`client.backtest_raw({...})`)
- Strict type hints + `py.typed` — first-class IDE and mypy support
- MIT licensed

---

## Common patterns

### Custom strategy

```python
from backtest360 import Client, Strategy, Execution, Costs, Risk, Sizing, Settings
import yfinance as yf

df = yf.download("SPY", start="2020-01-01", end="2024-01-01", interval="1d",
                 auto_adjust=False, multi_level_index=False, progress=False)
df.columns = df.columns.str.lower()

strat = Strategy(
    name="rsi_mean_reversion",
    long_entry="(rsi_14 > 30) & (rsi_14 < 50)",
    long_exit="rsi_14 >= 50",
    indicators=[Strategy.indicator("rsi", ref="rsi_14", period=14)],
)

result = Client(api_key="b360_...").backtest(
    strat, df,
    execution=Execution(entry="open", exit="close", signal_frequency="daily"),
    costs=Costs(slippage_bps=2.5, fee_pct=0.001),
    risk=Risk(stop="atr", value=2.5, atr_period=14, max_drawdown=0.25),
    sizing=Sizing(weight=1.0, vol_target=0.15, leverage_limit=2.0),
    settings=Settings(risk_free_rate=0.04),
)

result.summary()
print("Max Drawdown:", result.stats.get("max_drawdown", "n/a"))
for t in result.trades[:5]:
    print(t["entry_date"], t["direction"], t["return_net"])
```

> **Indicator library** (names, params, output columns):
> https://api.backtest360.com/docs
>
> **Strategy templates**: see the `Strategy.<name>()` classmethods (e.g.
> `Strategy.rsi_threshold_long()`).

### Built-in sample data

No data feed handy? The engine serves a small set of ready-made daily OHLCV
datasets, so you can run a backtest with no external dependency:

```python
from backtest360 import Client, Strategy

client = Client(api_key="b360_...")
print(client.sample_symbols())     # ['SPY', 'QQQ', 'BTC']
df = client.sample_data("BTC")     # DatetimeIndex + lowercase OHLCV — drop-in for backtest()
result = client.backtest(Strategy.rsi_threshold_long(), df)
```

### Pre-computed signals

```python
import pandas as pd
from backtest360 import Client

# Any signal series of {-1, 0, 1} — your ML model, custom indicator, etc.
signals = pd.Series(..., index=df.index)

result = Client(api_key="b360_...").backtest_signals(signals, df)
print(result.stats["sharpe"])
```

### Raw API escape hatch

For users who want exact control with the API docs open:

```python
resp = Client(api_key="...").backtest_raw({
    "run": {"stats_keys": "ids"},
    "legs": [
        {
            "id":          "main",
            "strategy":    {"condition_tree": {...}, "indicators": [...]},
            "data_source": {"ohlcv": {...}},
            "execution":   {"signal_frequency": "daily"},
        },
    ],
})
result = resp["legs"][0]["result"]
```

### Latest signal

Ask the engine for the target position on the most-recent bar — the live-trading hook:

```python
from backtest360 import Client, Strategy

signal = Client(api_key="b360_...").latest_signal(Strategy.rsi_threshold_long(), df)
position = {1: "LONG", 0: "FLAT", -1: "SHORT"}[signal["signal"]]
print(position, signal["long_entry_fired"])
```

### Validate a strategy

Check that a strategy is valid without running a backtest:

```python
from backtest360 import Client, Strategy

v = Client(api_key="b360_...").validate_strategy(Strategy.rsi_threshold_long())
print(v["valid"], v.get("errors", []))
```

### Discover indicators

```python
from backtest360 import Client

client = Client(api_key="b360_...")
print([i["name"] for i in client.list_indicators()])
```

### Discover strategy templates

Browse the predesigned templates available to your key, then fetch one in full
and run it:

```python
from backtest360 import Client

client = Client(api_key="b360_...")
for t in client.list_templates():                     # compact catalog
    print(t["id"], "-", t["description"])

strat = client.list_templates(name="rsi_mean_reversion")   # one full definition
df = client.sample_data("SPY")
resp = client.backtest_raw({
    "run": {"stats_keys": "ids"},
    "legs": [{"id": "main", "strategy": strat, "data_source": {"ohlcv": ...}}],
})
result = resp["legs"][0]["result"]
```

### Inspect your API key

See your key's scopes, rate limits, and current usage up front — so you can size
requests before hitting a limit:

```python
from backtest360 import Client

info = Client(api_key="b360_...").me()
print(info["scopes"])                       # ['backtest.run', 'meta.read', ...]
print(info["limits"]["rpm"], "req/min")
print(info["usage"]["day"]["remaining"], "requests left today")
```

### Error handling

```python
from backtest360 import Backtest360Error

try:
    result = client.backtest(strategy, df)
except Backtest360Error as e:
    if e.status == 401:
        print("Invalid or expired API key — renew at backtest360.com")
    elif e.status in (429, 503):
        # Rate limited or the engine is at capacity — retry with backoff.
        # e.retry_after carries the Retry-After header (seconds) when set.
        print(f"Busy — retry in {e.retry_after or 5:.0f}s")
    elif e.status == 504:
        print("Run exceeded the compute time limit — reduce the date range "
              "or strategy complexity")
    elif e.status == 422:
        detail = e.body.get("detail") if isinstance(e.body, dict) else e.body
        print("Strategy validation failed:", detail)
    else:
        raise   # unexpected — let it propagate
```

On error responses, `e.request_id` carries the response's `X-Request-ID` —
quote it when reporting an issue. You can also pass your own correlation id to
any method: `client.backtest(strategy, df, request_id="my-run-42")`.

---

## Versioning

`MAJOR.MINOR.PATCH`. Pre-1.0 (`0.x.y`): the API may move between minor versions.
Pre-release suffixes: `aN` (alpha), `bN` (beta), `rcN` (release candidate).
See [CHANGELOG.md](CHANGELOG.md) for the release history.

---

## Full documentation

- Engine API reference: https://api.backtest360.com/docs
- Runnable examples: [`examples/`](examples/)
- Release history: [CHANGELOG.md](CHANGELOG.md)

## Questions / feedback

Questions or feedback? hello@backtest360.com — we read everything. The client is in active development, so help shape it.

Bug reports and feature requests: [open an issue on GitHub](https://github.com/Backtest360/backtest360/issues).

## License

MIT — see [LICENSE](LICENSE).
