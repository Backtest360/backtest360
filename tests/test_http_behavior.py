"""Tests for HTTP request/response handling through a mocked transport."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest360 import Backtest360Error, Client, Costs, Execution, Result, Strategy

from conftest import make_response


def test_non_2xx_json_error_is_mapped(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(
        make_response(
            401,
            json={"detail": {"code": "ENGINE_BAD_KEY", "message": "Invalid API key"}},
            headers={"x-request-id": "req-123"},
        )
    )
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.status == 401
    assert exc.value.code == "ENGINE_BAD_KEY"
    assert exc.value.request_id == "req-123"
    assert exc.value.body == {"detail": {"code": "ENGINE_BAD_KEY", "message": "Invalid API key"}}
    assert "Invalid API key" in str(exc.value)


def test_headers_declare_client_contract(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json={"version": "1.0.0"}))
    client.version()
    headers = mock_engine.calls[0]["headers"]
    assert headers["X-Client-Contract"] == "1"
    assert headers["X-Client-Version"].startswith("backtest360/")
    # No correlation id was supplied, so none travels on the wire.
    assert "X-Request-ID" not in headers


def test_request_id_is_sent_when_provided(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json={"version": "1.0.0"}))
    client.version(request_id="my-run-42")
    assert mock_engine.calls[0]["headers"]["X-Request-ID"] == "my-run-42"


def test_retry_after_is_exposed_on_capacity_errors(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(
        make_response(
            503,
            json={"detail": {"code": "ENGINE_BUSY", "message": "At capacity"}},
            headers={"Retry-After": "30"},
        )
    )
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.status == 503
    assert exc.value.code == "ENGINE_BUSY"
    assert exc.value.retry_after == 30.0


def test_retry_after_is_none_when_header_absent(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(
        make_response(
            429, json={"detail": {"code": "QUOTA_EXCEEDED", "message": "Quota"}}
        )
    )
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.retry_after is None


def test_non_json_2xx_response_is_malformed(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, text="<html>not json</html>"))
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"
    assert exc.value.status == 200


def test_version_returns_engine_payload(client: Client, mock_engine) -> None:
    info = {
        "version": "1.0.0",
        "engine": "0.5.3",
        "api_contract": "1",
        "expected_client_contract": "1",
    }
    mock_engine.queue(make_response(200, json=info))
    assert client.version() == info


def test_version_non_dict_response_is_malformed(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=[1, 2, 3]))
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_latest_signal_unwraps_result_envelope(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(
        make_response(200, json={"result": {"signal": 1, "long_entry_fired": True}})
    )
    out = client.latest_signal(Strategy.rsi_threshold_long(), tiny_ohlcv)
    assert out == {"signal": 1, "long_entry_fired": True}


def test_latest_signal_returns_flat_dict_without_result_key(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    # A response with no "result" envelope is returned verbatim.
    mock_engine.queue(
        make_response(200, json={"signal": 1, "long_entry_fired": True})
    )
    out = client.latest_signal(Strategy.rsi_threshold_long(), tiny_ohlcv)
    assert out == {"signal": 1, "long_entry_fired": True}


def test_latest_signal_non_dict_response_is_malformed(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(make_response(200, json=[1, 2, 3]))
    with pytest.raises(Backtest360Error) as exc:
        client.latest_signal(Strategy.rsi_threshold_long(), tiny_ohlcv)
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_validate_strategy_sends_nested_strategy(client: Client, mock_engine) -> None:
    mock_engine.queue(
        make_response(
            200,
            json={"valid": True, "warmup_bars": 14, "referenced_indicators": ["rsi_14"]},
        )
    )
    out = client.validate_strategy(Strategy.rsi_threshold_long())
    assert out["valid"] is True
    assert out["warmup_bars"] == 14

    # The strategy definition travels nested under the "strategy" key.
    assert len(mock_engine.calls) == 1
    call = mock_engine.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/validate-strategy"
    assert set(call["json"]) == {"strategy"}
    assert "condition_tree" in call["json"]["strategy"]


def test_validate_strategy_returns_validation_failure(
    client: Client, mock_engine
) -> None:
    failure = {
        "valid": False,
        "errors": [
            {
                "code": "UNKNOWN_COLUMN_REF",
                "location": "/condition_tree/long_entry/",
                "message": "Unknown column reference(s): ['rsi_14']",
                "context": {"unknown": ["rsi_14"]},
            }
        ],
    }
    mock_engine.queue(make_response(422, json=failure))
    out = client.validate_strategy(Strategy.rsi_threshold_long())
    assert out["valid"] is False
    assert out["errors"][0]["code"] == "UNKNOWN_COLUMN_REF"


def test_backtest_builds_result_and_sends_ohlcv(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    payload = {
        "status": "success",
        "run": {"reference": None, "alignment": {}, "created_at": "2024-01-01T00:00:00Z"},
        "legs": [
            {
                "id": "strategy",
                "result": {
                    "stats": {"Sharpe": 1.42},
                    "series": {
                        "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
                        "strategy_equity": [1.0, 1.05, 1.1],
                    },
                },
            }
        ],
    }
    mock_engine.queue(make_response(200, json=payload))
    result = client.backtest(Strategy.rsi_threshold_long(), tiny_ohlcv)

    assert isinstance(result, Result)
    assert result.stats == {"Sharpe": 1.42}
    assert list(result.strategy_equity) == [1.0, 1.05, 1.1]

    # The outgoing request hit /api/backtest with the serialised OHLCV inside
    # the strategy leg.
    assert len(mock_engine.calls) == 1
    call = mock_engine.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/backtest"
    ohlcv = call["json"]["legs"][0]["data_source"]["ohlcv"]
    assert ohlcv["close"] == [101.0, 102.0, 103.0]
    assert len(ohlcv["dates"]) == 3
    assert call["headers"]["X-API-Key"] == "b360_test_key"


def test_backtest_merges_execution_knobs_and_benchmark(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {"reference": "benchmark"},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    },
                    {
                        "id": "benchmark",
                        "benchmark": True,
                        "result": {"series": {"dates": [], "strategy_equity": []}},
                    },
                ],
            },
        )
    )
    client.backtest(
        Strategy.rsi_threshold_long(), tiny_ohlcv,
        benchmark=tiny_ohlcv,
        execution=Execution(signal_frequency="daily"),
        costs=Costs(slippage_bps=2.5, fee_pct=0.001),
    )
    body = mock_engine.calls[0]["json"]
    # Execution + Costs knobs merge into one flat execution dict, nested
    # inside the strategy leg.
    strategy_leg = body["legs"][0]
    assert strategy_leg["execution"]["signal_frequency"] == "daily"
    assert strategy_leg["execution"]["slippage_bps"] == 2.5
    assert strategy_leg["execution"]["fee_pct"] == 0.001
    # Benchmark OHLCV travels as its own leg, with run.reference pointing at it.
    benchmark_leg = body["legs"][1]
    assert benchmark_leg["id"] == "benchmark"
    assert benchmark_leg["benchmark"] is True
    assert benchmark_leg["data_source"]["ohlcv"]["close"] == [101.0, 102.0, 103.0]
    assert body["run"]["reference"] == "benchmark"


def test_backtest_omits_execution_and_benchmark_when_unset(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    }
                ],
            },
        )
    )
    client.backtest(Strategy.rsi_threshold_long(), tiny_ohlcv)
    body = mock_engine.calls[0]["json"]
    assert "execution" not in body["legs"][0]
    assert len(body["legs"]) == 1
    assert "reference" not in body["run"]


def test_backtest_defaults_stats_keys_to_ids(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    }
                ],
            },
        )
    )
    client.backtest(Strategy.rsi_threshold_long(), tiny_ohlcv)
    assert mock_engine.calls[0]["json"]["run"]["stats_keys"] == "ids"


def test_backtest_stats_keys_can_be_overridden_to_labels(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    }
                ],
            },
        )
    )
    client.backtest(Strategy.rsi_threshold_long(), tiny_ohlcv, stats_keys="labels")
    assert mock_engine.calls[0]["json"]["run"]["stats_keys"] == "labels"


def test_backtest_signals_defaults_stats_keys_to_ids(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    signals = pd.Series([0, 1, 0], index=tiny_ohlcv.index)
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    }
                ],
            },
        )
    )
    client.backtest_signals(signals, tiny_ohlcv)
    assert mock_engine.calls[0]["json"]["run"]["stats_keys"] == "ids"


def test_backtest_raw_sends_payload_verbatim(client: Client, mock_engine) -> None:
    payload = {
        "strategy": {"condition_tree": {}, "indicators": []},
        "data_source": {"ohlcv": {"dates": []}},
        "execution": {"signal_frequency": "daily"},
    }
    mock_engine.queue(make_response(200, json={"result": {"stats": {"Sharpe": 1.0}}}))
    out = client.backtest_raw(payload)
    call = mock_engine.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/backtest"
    assert call["json"] == payload  # sent verbatim, no client-side mutation
    assert out == {"result": {"stats": {"Sharpe": 1.0}}}


def test_backtest_raw_non_dict_response_is_malformed(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=[1, 2, 3]))
    with pytest.raises(Backtest360Error) as exc:
        client.backtest_raw({"x": 1})
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_backtest_signals_index_mismatch_rejected_before_request(
    client: Client, tiny_ohlcv
) -> None:
    # Different index than ohlcv — must raise before any wire serialisation.
    signals = pd.Series([0, 1], index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    with pytest.raises(Backtest360Error) as exc:
        client.backtest_signals(signals, tiny_ohlcv)
    assert exc.value.code == "CLIENT_INVALID_SIGNALS"
    assert exc.value.status == 0


def test_backtest_signals_sends_values_and_coerces_bools(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    signals = pd.Series([True, False, True], index=tiny_ohlcv.index)
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {"reference": "benchmark"},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {"stats": {}, "series": {"dates": [], "strategy_equity": []}},
                    },
                    {
                        "id": "benchmark",
                        "benchmark": True,
                        "result": {"series": {"dates": [], "strategy_equity": []}},
                    },
                ],
            },
        )
    )
    result = client.backtest_signals(signals, tiny_ohlcv, name="mysig", benchmark=tiny_ohlcv)
    assert isinstance(result, Result)
    body = mock_engine.calls[0]["json"]
    strategy_leg = body["legs"][0]
    assert strategy_leg["signals"]["values"] == [1, 0, 1]  # bool -> int
    assert strategy_leg["signals"]["strategy_name"] == "mysig"
    assert "ohlcv" in strategy_leg["data_source"]
    assert "ohlcv" in body["legs"][1]["data_source"]


def test_list_indicators_list_passthrough(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=[{"name": "rsi"}, {"name": "sma"}]))
    out = client.list_indicators()
    assert out == [{"name": "rsi"}, {"name": "sma"}]
    call = mock_engine.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/indicators"


def test_list_indicators_unwraps_dict_envelope(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json={"indicators": [{"name": "macd"}]}))
    assert client.list_indicators() == [{"name": "macd"}]


def test_list_indicators_malformed_type_raises(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json="not-a-list-or-object"))
    with pytest.raises(Backtest360Error) as exc:
        client.list_indicators()
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def _templates_response() -> dict:
    """A two-template ``/api/strategies`` payload with full entries."""
    return {
        "strategies": [
            {
                "id": "rsi_mean_reversion",
                "origin": "system",
                "name": "rsi_mean_reversion",
                "description": "Buy oversold RSI, sell overbought.",
                "condition_tree": {"long_entry": {"op": "leaf", "expr": "rsi_14 < 30"}},
                "indicators": [{"ref": "rsi_14", "name": "rsi", "params": {"period": 14}}],
                "requires": {},
                "defaults": {"open_hour": 9.5},
                "locked_params": [],
            },
            {
                "id": "ma_crossover",
                "origin": "system",
                "name": "ma_crossover",
                "description": "Golden/death cross of two moving averages.",
                "condition_tree": {"long_entry": {"op": "leaf", "expr": "sma_50 > sma_200"}},
                "indicators": [{"ref": "sma_50", "name": "sma", "params": {"period": 50}}],
                "requires": {},
                "defaults": {},
                "locked_params": [],
            },
        ]
    }


def test_list_templates_compact_is_default(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=_templates_response()))
    out = client.list_templates()
    call = mock_engine.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/strategies"
    # Compact entries carry only the discovery fields — no strategy logic.
    assert out == [
        {
            "id": "rsi_mean_reversion",
            "origin": "system",
            "name": "rsi_mean_reversion",
            "description": "Buy oversold RSI, sell overbought.",
        },
        {
            "id": "ma_crossover",
            "origin": "system",
            "name": "ma_crossover",
            "description": "Golden/death cross of two moving averages.",
        },
    ]
    for entry in out:
        assert "condition_tree" not in entry


def test_list_templates_full_when_compact_false(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=_templates_response()))
    out = client.list_templates(compact=False)
    assert isinstance(out, list) and len(out) == 2
    # Full entries keep the strategy logic and parameter metadata.
    assert out[0]["condition_tree"]["long_entry"]["expr"] == "rsi_14 < 30"
    assert out[0]["indicators"][0]["ref"] == "rsi_14"
    assert "defaults" in out[0]


def test_list_templates_by_name_returns_full_entry(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=_templates_response()))
    # Case-insensitive match on id or name; returns a single full dict.
    out = client.list_templates(name="MA_CROSSOVER")
    assert isinstance(out, dict)
    assert out["id"] == "ma_crossover"
    assert out["condition_tree"]["long_entry"]["expr"] == "sma_50 > sma_200"


def test_list_templates_unknown_name_raises(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=_templates_response()))
    with pytest.raises(Backtest360Error) as exc:
        client.list_templates(name="does_not_exist")
    assert exc.value.code == "CLIENT_TEMPLATE_NOT_FOUND"
    assert exc.value.status == 0


def test_list_templates_malformed_response_raises(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json={"not_strategies": []}))
    with pytest.raises(Backtest360Error) as exc:
        client.list_templates()
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_list_templates_no_kwargs_sends_no_query_string(
    client: Client, mock_engine
) -> None:
    # No new filter/paging kwarg given -> the request must be byte-identical
    # to the call before filtering/paging existed: no query string at all.
    mock_engine.queue(make_response(200, json=_templates_response()))
    client.list_templates()
    call = mock_engine.calls[0]
    assert call["path"] == "/api/strategies"
    assert call["query"] == ""


def test_list_templates_filter_and_paging_kwargs_build_exact_query_string(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(make_response(200, json=_templates_response()))
    client.list_templates(
        collection="momentum",
        q="rsi",
        tags=["mean-reversion", "long-only"],
        detail="full",
        limit=20,
        offset=40,
    )
    call = mock_engine.calls[0]
    assert call["path"] == "/api/strategies"
    assert call["query"] == (
        "collection=momentum&q=rsi&tags=mean-reversion%2Clong-only"
        "&detail=full&limit=20&offset=40"
    )


def test_list_templates_tags_accepts_a_single_string(
    client: Client, mock_engine
) -> None:
    # A single tag as a plain string is sent as-is, not iterated character by
    # character.
    mock_engine.queue(make_response(200, json=_templates_response()))
    client.list_templates(tags="momentum")
    call = mock_engine.calls[0]
    assert call["query"] == "tags=momentum"


def test_list_templates_only_given_kwargs_appear_on_the_wire(
    client: Client, mock_engine
) -> None:
    # Kwargs left at their default (None) are omitted entirely, not sent as
    # empty values.
    mock_engine.queue(make_response(200, json=_templates_response()))
    client.list_templates(collection="all", limit=10)
    call = mock_engine.calls[0]
    assert call["query"] == "collection=all&limit=10"


def _paged_templates_response() -> dict:
    """A ``/api/strategies`` payload carrying pagination envelope keys."""
    resp = _templates_response()
    resp["strategies"][0]["collection"] = "curated"
    resp["strategies"][0]["tags"] = ["mean-reversion", "oscillator"]
    resp["count"] = 2
    resp["total"] = 57
    resp["next_offset"] = 2
    return resp


def test_list_templates_raw_returns_full_envelope(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(make_response(200, json=_paged_templates_response()))
    out = client.list_templates(raw=True)
    assert isinstance(out, dict)
    assert out["count"] == 2
    assert out["total"] == 57
    assert out["next_offset"] == 2
    assert len(out["strategies"]) == 2


def test_list_templates_full_entries_expose_collection_and_tags(
    client: Client, mock_engine
) -> None:
    mock_engine.queue(make_response(200, json=_paged_templates_response()))
    out = client.list_templates(compact=False)
    assert isinstance(out, list)
    assert out[0]["collection"] == "curated"
    assert out[0]["tags"] == ["mean-reversion", "oscillator"]


def test_list_templates_by_name_ignores_raw(client: Client, mock_engine) -> None:
    # `raw` only applies to the list form; a name lookup still returns the
    # single matched template dict.
    mock_engine.queue(make_response(200, json=_paged_templates_response()))
    out = client.list_templates(name="rsi_mean_reversion", raw=True)
    assert isinstance(out, dict)
    assert out["id"] == "rsi_mean_reversion"
    assert "strategies" not in out


def test_me_returns_key_introspection(client: Client, mock_engine) -> None:
    payload = {
        "scopes": ["backtest.run", "meta.read"],
        "limits": {"rpm": 20, "rpd": 500, "max_concurrent": 2, "max_bars_per_run": 50000},
        "usage": {
            "minute": {"used": 3, "remaining": 17, "resets_in_seconds": 42},
            "day": {"used": 120, "remaining": 380, "resets_in_seconds": 51234},
            "concurrent": {"used": 1, "remaining": 1},
        },
        "capabilities": {"server_side_fetch": False, "full_metrics": False},
    }
    mock_engine.queue(make_response(200, json=payload))
    out = client.me()
    assert out == payload
    call = mock_engine.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/me"


def test_me_non_dict_response_is_malformed(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(200, json=["not", "a", "dict"]))
    with pytest.raises(Backtest360Error) as exc:
        client.me()
    assert exc.value.code == "CLIENT_MALFORMED_RESPONSE"


def test_default_base_url_when_unset() -> None:
    # With no base_url arg and no env var (clean_env autouse), the default applies.
    c = Client(api_key="b360_test_key")
    assert c._base_url == "https://api.backtest360.com"


def test_base_url_from_env_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKTEST360_ENGINE_URL", "https://my.engine.test/")
    c = Client(api_key="b360_test_key")
    assert c._base_url == "https://my.engine.test"


def test_api_key_resolved_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKTEST360_API_KEY", "env-key")
    c = Client()
    assert c._api_key == "env-key"


def test_error_string_detail_is_used_as_message(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(500, json={"detail": "Engine exploded"}))
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.status == 500
    assert exc.value.code is None
    assert "Engine exploded" in str(exc.value)


def test_error_list_detail_is_joined(client: Client, mock_engine) -> None:
    mock_engine.queue(
        make_response(400, json={"detail": [{"loc": ["body", "x"], "msg": "bad value"}]})
    )
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert "body -> x: bad value" in str(exc.value)


def test_error_top_level_error_field(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(400, json={"error": "top-level boom"}))
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert "top-level boom" in str(exc.value)


def test_error_non_json_body_is_used_as_text(client: Client, mock_engine) -> None:
    mock_engine.queue(make_response(502, text="Bad Gateway"))
    with pytest.raises(Backtest360Error) as exc:
        client.version()
    assert exc.value.status == 502
    assert exc.value.body == "Bad Gateway"
    assert "Bad Gateway" in str(exc.value)


def test_non_serializable_payload_rejected_before_request(client: Client) -> None:
    # NaN cannot be encoded with allow_nan=False — caught before any wire call.
    with pytest.raises(Backtest360Error) as exc:
        client.backtest_raw({"value": float("nan")})
    assert exc.value.code == "CLIENT_INVALID_PAYLOAD"
    assert exc.value.status == 0


def test_backtest_with_benchmark_exposes_relative_and_benchmark_equity(
    client: Client, mock_engine, tiny_ohlcv
) -> None:
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    mock_engine.queue(
        make_response(
            200,
            json={
                "status": "success",
                "run": {"stats_keys": "ids"},
                "legs": [
                    {
                        "id": "strategy",
                        "result": {
                            "stats": {"sharpe": 1.1},
                            "series": {
                                "dates": dates,
                                "strategy_equity": [1.0, 1.02, 1.05],
                            },
                        },
                        "relative": {"beta": 1.1, "alpha": 0.02},
                    },
                    {
                        "id": "benchmark",
                        "result": {
                            "series": {
                                "dates": dates,
                                "strategy_equity": [1.0, 1.01, 1.03],
                            },
                        },
                    },
                ],
            },
        )
    )
    result = client.backtest(Strategy.rsi_threshold_long(), tiny_ohlcv, benchmark=tiny_ohlcv)

    assert result.relative == {"beta": 1.1, "alpha": 0.02}
    assert "beta" not in result.stats

    bench_eq = result.benchmark_equity
    assert len(bench_eq) > 0
    assert list(bench_eq) == [1.0, 1.01, 1.03]
