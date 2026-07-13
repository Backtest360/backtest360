"""Tests for OHLCV wire serialisation — the client's request-side boundary."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest360 import Backtest360Error, Client, Strategy
from backtest360.client import _ohlcv_to_wire, _signals_to_wire

from conftest import make_response, tiny_ohlcv


def test_happy_path_serialises_parallel_arrays() -> None:
    df = tiny_ohlcv()
    wire = _ohlcv_to_wire(df)
    assert set(wire) == {"dates", "open", "high", "low", "close"}
    assert wire["dates"] == ["2024-01-01T00:00:00", "2024-01-02T00:00:00", "2024-01-03T00:00:00"]
    assert wire["open"] == [100.0, 101.0, 102.0]
    assert wire["high"] == [102.0, 103.0, 104.0]
    assert wire["low"] == [99.0, 100.0, 101.0]
    assert wire["close"] == [101.0, 102.0, 103.0]
    # Volume is omitted when absent and included when present.
    assert "volume" not in wire
    df_vol = df.copy()
    df_vol["volume"] = [10.0, 20.0, 30.0]
    wire_vol = _ohlcv_to_wire(df_vol)
    assert wire_vol["volume"] == [10.0, 20.0, 30.0]


def test_nan_value_rejected() -> None:
    df = tiny_ohlcv()
    df.loc[df.index[1], "close"] = np.nan
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"


def test_infinity_value_rejected() -> None:
    df = tiny_ohlcv()
    df.loc[df.index[0], "high"] = np.inf
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"


def test_empty_frame_rejected() -> None:
    df = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": []},
        index=pd.DatetimeIndex([]),
    )
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"


def test_non_datetime_index_and_missing_column_rejected() -> None:
    # Non-DatetimeIndex is rejected.
    df = tiny_ohlcv().reset_index(drop=True)
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"

    # A missing required column is rejected and the column is named.
    df_missing = tiny_ohlcv().drop(columns=["close"])
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df_missing)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"
    assert "close" in str(exc.value)


def test_oversized_ohlcv_rejected() -> None:
    n = 1_000_001
    ones = np.ones(n)
    df = pd.DataFrame(
        {"open": ones, "high": ones, "low": ones, "close": ones},
        index=pd.date_range("2000-01-01", periods=n, freq="min"),
    )
    with pytest.raises(Backtest360Error) as exc:
        _ohlcv_to_wire(df)
    assert exc.value.code == "CLIENT_INVALID_OHLCV"
    assert "limit" in str(exc.value)


def test_oversized_signals_rejected() -> None:
    n = 1_000_001
    signals = pd.Series(
        np.zeros(n, dtype=int),
        index=pd.date_range("2000-01-01", periods=n, freq="min"),
    )
    with pytest.raises(Backtest360Error) as exc:
        _signals_to_wire(signals, None)
    assert exc.value.code == "CLIENT_INVALID_SIGNALS"
    assert "limit" in str(exc.value)


def _three_dates() -> pd.DatetimeIndex:
    return pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])


def test_signals_nan_or_inf_rejected() -> None:
    for bad in (np.nan, np.inf):
        s = pd.Series([0, bad, 1], index=_three_dates())
        with pytest.raises(Backtest360Error) as exc:
            _signals_to_wire(s, None)
        assert exc.value.code == "CLIENT_INVALID_SIGNALS"


def test_signals_non_numeric_rejected() -> None:
    s = pd.Series(["a", "b", "c"], index=_three_dates())
    with pytest.raises(Backtest360Error) as exc:
        _signals_to_wire(s, None)
    assert exc.value.code == "CLIENT_INVALID_SIGNALS"


def test_signals_out_of_range_or_fractional_rejected() -> None:
    for bad in (2, -2, 0.5):
        s = pd.Series([0, bad, 1], index=_three_dates())
        with pytest.raises(Backtest360Error) as exc:
            _signals_to_wire(s, None)
        assert exc.value.code == "CLIENT_INVALID_SIGNALS"


def test_signals_non_datetime_index_rejected() -> None:
    s = pd.Series([0, 1, -1])  # default RangeIndex
    with pytest.raises(Backtest360Error) as exc:
        _signals_to_wire(s, None)
    assert exc.value.code == "CLIENT_INVALID_SIGNALS"


def test_signals_happy_path_coerces_bools_and_adds_name() -> None:
    s = pd.Series([True, False, True], index=_three_dates())
    wire = _signals_to_wire(s, "mysig")
    assert wire["values"] == [1, 0, 1]
    assert wire["strategy_name"] == "mysig"
    assert wire["dates"] == [
        "2024-01-01T00:00:00", "2024-01-02T00:00:00", "2024-01-03T00:00:00"
    ]
    # strategy_name is omitted when not supplied.
    assert "strategy_name" not in _signals_to_wire(pd.Series([1, 0, -1], index=_three_dates()), None)


def test_sample_symbols_returns_symbol_list(client: Client, mock_engine) -> None:
    mock_engine.queue(
        make_response(200, json={"status": "success", "symbols": ["SPY", "QQQ", "BTC"]})
    )
    assert client.sample_symbols() == ["SPY", "QQQ", "BTC"]
    call = mock_engine.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/data/samples"


def test_sample_data_parses_ohlcv_into_datetime_frame(
    client: Client, mock_engine
) -> None:
    # tz-naive ISO dates so the round-trip back through _ohlcv_to_wire is exact.
    ohlcv = {
        "dates": ["2025-01-02T00:00:00", "2025-01-03T00:00:00", "2025-01-06T00:00:00"],
        "open":  [100.0, 101.0, 102.0],
        "high":  [102.0, 103.0, 104.0],
        "low":   [99.0, 100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
        "volume": [10.0, 20.0, 30.0],
    }
    mock_engine.queue(
        make_response(
            200,
            json={"status": "success", "summary": {"source": "sample"}, "ohlcv": ohlcv},
        )
    )
    df = client.sample_data("SPY")

    # GET hit the sample endpoint with the symbol on the query string.
    call = mock_engine.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/data/sample"

    # DatetimeIndex + lowercase OHLCV columns — drop-in for backtest().
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index[0]) == "2025-01-02 00:00:00"
    assert df["close"].tolist() == [101.0, 102.0, 103.0]

    # Round-trips cleanly back through the existing request-side wire helper.
    assert _ohlcv_to_wire(df) == ohlcv


def test_sample_data_missing_ohlcv_block_is_malformed(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(make_response(200, json={"status": "success"}))
    with pytest.raises(Backtest360Error) as exc:
        client.sample_data("SPY")
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_too_many_indicators_rejected_before_any_request() -> None:
    # No fake transport is installed: a real request would fail differently.
    # The bounds check must fire first.
    client = Client(api_key="b360_test_key", base_url="https://engine.example.test")
    strat = Strategy(
        name="too_big",
        long_entry="s0 > 0",
        indicators=[
            Strategy.indicator("sma", ref=f"s{i}", period=i + 1) for i in range(129)
        ],
    )
    with pytest.raises(Backtest360Error) as exc:
        client.validate_strategy(strat)
    assert exc.value.code == "CLIENT_INVALID_STRATEGY"
    assert exc.value.status == 0


def test_oversized_expression_rejected_before_any_request() -> None:
    client = Client(api_key="b360_test_key", base_url="https://engine.example.test")
    strat = Strategy(
        name="long_expr",
        long_entry="(rsi_14 < 30) | " * 64 + "(rsi_14 < 30)",  # > 512 chars
        indicators=[Strategy.indicator("rsi", ref="rsi_14", period=14)],
    )
    with pytest.raises(Backtest360Error) as exc:
        client.validate_strategy(strat)
    assert exc.value.code == "CLIENT_INVALID_STRATEGY"
    assert "long_entry" in str(exc.value)
