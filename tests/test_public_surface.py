"""Tests that lock the public surface of the package.

These guard against accidental renames or additions to the customer-facing API.
"""

from __future__ import annotations

import importlib

import pytest

import backtest360
from backtest360 import Backtest360Error, Client, Result
from backtest360.client import _fmt_pct, _fmt_ratio

# The complete public API, locked. Adding or removing a name is a breaking
# change and must update this list deliberately.
EXPECTED_ALL = sorted(
    [
        "Client",
        "Strategy",
        "Execution",
        "Costs",
        "Risk",
        "Sizing",
        "MarketHours",
        "Settings",
        "Result",
        "Backtest360Error",
        "__version__",
    ]
)

EXPECTED_CLIENT_METHODS = {
    "backtest",
    "backtest_signals",
    "backtest_raw",
    "latest_signal",
    "validate_strategy",
    "list_indicators",
    "list_templates",
    "me",
    "version",
    "sample_symbols",
    "sample_data",
}


def test_all_is_locked_and_importable() -> None:
    assert sorted(backtest360.__all__) == EXPECTED_ALL
    assert len(EXPECTED_ALL) == 11
    for name in backtest360.__all__:
        assert hasattr(backtest360, name), f"{name} is in __all__ but not importable"


def test_client_public_methods_are_locked() -> None:
    public = {
        name
        for name in dir(Client)
        if not name.startswith("_") and callable(getattr(Client, name))
    }
    assert public == EXPECTED_CLIENT_METHODS


def test_version_is_nonempty_string() -> None:
    assert isinstance(backtest360.__version__, str)
    assert backtest360.__version__ != ""


def test_result_exposes_strategy_equity_not_equity() -> None:
    # Build a Result the way client.py builds one: from the inner result dict.
    data = {
        "series": {
            "dates": ["2024-01-01", "2024-01-02"],
            "strategy_equity": [1.0, 1.1],
        }
    }
    result = Result(data)
    eq = result.strategy_equity
    assert list(eq) == [1.0, 1.1]
    with pytest.raises(AttributeError):
        _ = result.equity


def test_result_series_unparseable_dates_raise_malformed() -> None:
    # An engine date the client cannot parse must surface as a Backtest360Error,
    # not a raw pandas error leaking out of a public property.
    result = Result(
        {"series": {"dates": ["not-a-date"], "strategy_equity": [1.0]}}
    )
    with pytest.raises(Backtest360Error) as exc:
        _ = result.strategy_equity
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"
    assert exc.value.status == 0


def test_result_series_length_mismatch_raises_malformed() -> None:
    # More values than dates is a malformed engine response, not a raw
    # pandas length error.
    result = Result(
        {"series": {"dates": ["2024-01-01", "2024-01-02"], "strategy_equity": [1.0, 1.1, 1.2]}}
    )
    with pytest.raises(Backtest360Error) as exc:
        _ = result.strategy_equity
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_result_exposes_markers_and_data_quality() -> None:
    result = Result(
        {
            "markers": {"warmup_bars": 14, "first_trade_date": "2020-03-10"},
            "data_quality": {"missing_bars": [], "quality_warnings": []},
        }
    )
    assert result.markers == {"warmup_bars": 14, "first_trade_date": "2020-03-10"}
    assert result.data_quality == {"missing_bars": [], "quality_warnings": []}

    # Both default to an empty dict when the response carries no block.
    empty = Result({})
    assert empty.markers == {}
    assert empty.data_quality == {}


def test_result_properties_from_full_response() -> None:
    data = {
        "stats": {"sharpe": 1.42, "cagr": 0.121},
        "trades": [{"entry_date": "2024-01-01", "return_net": 0.05}],
        "series": {
            "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "strategy_equity": [1.0, 1.1, 1.2],
            "benchmark_equity": [1.0, 1.05, 1.08],
            "returns": [0.0, 0.1, 0.09],
            "signals": [0, 1, 1],
        },
    }
    r = Result(data)
    assert r.stats["sharpe"] == 1.42
    assert r.trades[0]["return_net"] == 0.05
    assert list(r.strategy_equity) == [1.0, 1.1, 1.2]
    assert list(r.benchmark_equity) == [1.0, 1.05, 1.08]
    assert list(r.returns) == [0.0, 0.1, 0.09]
    assert list(r.signals) == [0, 1, 1]
    assert str(r.strategy_equity.index[0]) == "2024-01-01 00:00:00"
    assert r.raw is data


def test_result_defaults_when_blocks_absent() -> None:
    r = Result({})
    assert r.stats == {}
    assert r.trades == []
    assert list(r.strategy_equity) == []
    # Benchmark is optional — absent renders an empty Series, not an error.
    assert list(r.benchmark_equity) == []


def test_summary_computes_total_return_and_formats(capsys: pytest.CaptureFixture[str]) -> None:
    data = {
        "stats": {"cagr": 0.121, "vol_ann": 0.184, "sharpe": 1.42},
        "series": {"dates": ["2024-01-01", "2024-01-02"], "strategy_equity": [1.0, 1.5]},
    }
    Result(data).summary()
    out = capsys.readouterr().out
    assert "Performance Summary" in out
    assert "Total Return" in out and "50.0%" in out  # 1.5 / 1.0 - 1
    assert "12.1%" in out  # CAGR
    assert "18.4%" in out  # Vol (Ann)
    assert "1.42" in out   # Sharpe


def test_summary_reads_label_keyed_stats(capsys: pytest.CaptureFixture[str]) -> None:
    # Label-keyed stats (returned for stats_keys="labels", and by any engine
    # predating api_contract 3 that ignores the stats_keys field) must render
    # the same four rows as id-keyed stats.
    data = {
        "stats": {"CAGR": 0.121, "Vol (Ann)": 0.184, "Sharpe": 1.42},
        "series": {"dates": ["2024-01-01", "2024-01-02"], "strategy_equity": [1.0, 1.5]},
    }
    Result(data).summary()
    out = capsys.readouterr().out
    assert "n/a" not in out
    assert "Total Return" in out and "50.0%" in out  # 1.5 / 1.0 - 1
    assert "12.1%" in out  # CAGR
    assert "18.4%" in out  # Vol (Ann)
    assert "1.42" in out   # Sharpe


def test_summary_renders_na_for_missing_stats(capsys: pytest.CaptureFixture[str]) -> None:
    # No stats and a single-point equity (no total return) -> all four metrics n/a.
    Result({"series": {"dates": ["2024-01-01"], "strategy_equity": [1.0]}}).summary()
    out = capsys.readouterr().out
    assert out.count("n/a") == 4


def test_client_without_api_key_raises() -> None:
    with pytest.raises(Backtest360Error) as exc:
        Client()
    assert exc.value.code == "CLIENT_NO_API_KEY"
    assert exc.value.status == 0


def test_unsupported_path_rejected_before_any_request() -> None:
    # No fake transport is installed: if the client attempted a real request it
    # would fail differently. The path check must fire first.
    client = Client(api_key="b360_test_key", base_url="https://engine.example.test")
    with pytest.raises(Backtest360Error) as exc:
        client._request("GET", "/api/unsupported")
    assert exc.value.code == "CLIENT_PATH_FORBIDDEN"
    assert exc.value.status == 0


def test_summary_formatters_render_non_finite_as_na() -> None:
    # NaN/inf stats must render as 'n/a' in summary(), not 'nan%'/'nan'/'inf%'.
    for bad in (float("nan"), float("inf"), float("-inf"), None, "x", True):
        assert _fmt_pct(bad) == "n/a"
        assert _fmt_ratio(bad) == "n/a"
    # Finite values still format normally.
    assert _fmt_pct(0.089) == "8.9%"
    assert _fmt_ratio(1.42) == "1.42"


def test_package_reimport_is_stable() -> None:
    # Sanity: re-importing yields the same locked surface.
    reloaded = importlib.import_module("backtest360")
    assert sorted(reloaded.__all__) == EXPECTED_ALL
