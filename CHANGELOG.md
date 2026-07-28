# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- `Client.list_templates()` now accepts `collection`, `q`, `tags`, `detail`,
  `limit`, and `offset` to filter and page through the strategy-template
  catalog server-side, plus `raw=True` to get back the response envelope
  (`count`, `total`, `next_offset`) needed to fetch further pages. Templates
  also carry `collection` and `tags` fields when returned in full
  (`compact=False`). Existing calls are unaffected — every new argument
  defaults to unset, and the default request/response shape is unchanged.

---

## [0.7.0] — 2026-07-18

### Added

- `Result.relative` — benchmark-relative metrics dict (Alpha, Beta, Information Ratio,
  Tracking Error, Up/Down Capture, Capture Ratio), keyed per the request's `stats_keys`;
  empty when no benchmark was supplied.

### Fixed

- Benchmark-relative metrics are now surfaced to callers. The engine returns them in a
  dedicated per-leg `relative` block, which the client now exposes as `result.relative`;
  previously the block was not exposed and the metrics were unavailable from a `Result`.
- Docstrings and the benchmark example now direct readers to `result.relative` for
  benchmark-relative metrics; `result.stats` never contains them.
- `README.md` raw-API examples now show the `{"run": {...}, "legs": [...]}` request
  envelope and leg-based response introduced in 0.6.1.

---

## [0.6.1] — 2026-07-13

Initial public release of the recreated repository (fresh history; the package lineage
continues from 0.6.0).

### Breaking

- `Client.backtest()`, `Client.backtest_signals()`, and `Client.backtest_raw()` now speak
  the engine's contract-4 `{run, legs}` wire shape. The request envelope is
  `{"run": {...}, "legs": [...]}` and the response is `{"status", "run", "legs"}`; a single
  backtest is one leg and its result is `legs[0]["result"]`. Raw payloads passed to
  `backtest_raw()` must adopt the new shape.
- Benchmark runs now seed at bar 0 and are cross-leg-aligned with the strategy, so
  benchmark-relative numbers shift versus prior releases.

---

## [0.6.0] — 2026-07-05

### Added

- `Client.list_templates(name=None, compact=True)` — list the predesigned
  strategy templates available to your key (tier-filtered server-side). Returns
  a compact catalog by default (`id`, `origin`, `name`, `description`); pass
  `compact=False` for every template's full definition, or `name=<id-or-name>`
  (case-insensitive) for one template's full entry — a ready-to-run
  `condition_tree` + `indicators` plus `requires` / `defaults` /
  `locked_params` metadata.
- `Client.me()` — introspect the calling API key: granted scopes, numeric
  limits (`rpm`, `rpd`, `max_concurrent`, `max_bars_per_run`), current usage
  with reset countdowns, and capability flags. Discover what a key can do up
  front instead of from error responses.

### Fixed

- `Result.summary()` now renders Annualized Return, Annualized Std Dev, and
  Sharpe when the engine returns label-keyed stats (from `stats_keys="labels"`,
  or from an engine that predates the id-keyed stats contract and ignores the
  `stats_keys` request field). Each metric is read id-first with a display-label
  fallback; the id-keyed default path is unchanged.

### Breaking

- `Client.backtest()` and `Client.backtest_signals()` now default to
  `stats_keys="ids"`, so `result.stats` is keyed by each metric's stable
  snake_case id (e.g. `"sharpe"`) instead of its display label (e.g.
  `"Sharpe"`). Pass `stats_keys="labels"` to keep the legacy label keys. See
  the metrics catalog on `GET /api/sections` for the full id/label mapping.
  `Client.backtest_raw()` is unaffected — it sends payloads verbatim, so set
  `stats_keys` explicitly in the payload if you use it.

---

## [0.5.0] — 2026-06-29

Public release.

### Added

- `Client` — synchronous HTTP wrapper over the public Backtest360 API.
  Methods: `backtest`, `backtest_signals`, `backtest_raw`, `latest_signal`,
  `validate_strategy`, `list_indicators`, `version`, `sample_symbols`, `sample_data`.
- `Client.sample_symbols()` and `Client.sample_data(symbol="SPY")` — retrieve the
  engine's bundled sample datasets (`SPY`, `QQQ`, `BTC`). `sample_data()` returns a
  `DatetimeIndex`-indexed DataFrame with lowercase OHLCV columns, ready to pass straight
  to `backtest()`, `backtest_signals()`, or `latest_signal()`.
- `Strategy` — strategy builder with boolean expression strings
  (`long_entry`, `long_exit`, `short_entry`, `short_exit`) and indicator descriptors.
  Pre-built templates: `rsi_threshold_long`, `rsi_mean_reversion`, `ma_crossover`,
  `momentum_6m_long`.
- Configuration dataclasses: `Execution`, `Costs`, `Risk`, `Sizing`, `MarketHours`, `Settings`.
- `Result` — response wrapper with `stats`, `trades`, `strategy_equity`,
  `benchmark_equity`, `returns`, `signals`, `markers`, `data_quality`, `summary()`, and `raw`.
- `Backtest360Error` — single exception with `status`, `code`, `body`, and `request_id`,
  machine-readable `CLIENT_*` codes, and `retry_after` parsed from the `Retry-After` header on
  429 and 503 responses.
- `X-Client-Contract` request header declaring the API contract version the client targets; the
  engine rejects the call with HTTP 409 when it no longer supports that contract.
- Optional `request_id` argument on every method, sent as the `X-Request-ID` header and echoed
  by the engine for log correlation.
- Client-side request validation: OHLCV, signal, and strategy inputs that exceed the engine's
  limits are rejected before any request is sent; dates are serialised in ISO 8601 format.
- `BACKTEST360_API_KEY` and `BACKTEST360_ENGINE_URL` environment variables.
- `py.typed` marker — first-class mypy / pyright support.
- Mock-based smoke test suite, CI, runnable examples, and governance files
  (`SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`).
- MIT license.

<!-- Link references -->

[0.7.0]: https://github.com/Backtest360/backtest360/releases/tag/v0.7.0
[0.6.1]: https://github.com/Backtest360/backtest360/releases/tag/v0.6.1
