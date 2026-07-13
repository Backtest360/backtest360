"""Tests for the Strategy builder and execution-config dataclasses.

These lock the ``to_wire()`` contracts — field renames and omit-when-None
semantics — that the engine depends on, plus the pre-built templates.
"""

from __future__ import annotations

from backtest360 import (
    Costs,
    Execution,
    MarketHours,
    Risk,
    Settings,
    Sizing,
    Strategy,
)


# ---------------------------------------------------------------------------
# Dataclass to_wire() contracts
# ---------------------------------------------------------------------------


def test_execution_to_wire_renames_and_defaults() -> None:
    assert Execution().to_wire() == {
        "signal_frequency": "daily",
        "entry_anchor": "open",
        "entry_window": 0,
        "entry_fill": "exact",
        "exit_anchor": "close",
        "exit_window": 0,
        "exit_fill": "exact",
    }
    # entry -> entry_anchor, exit -> exit_anchor.
    wire = Execution(entry="vwap", exit="open").to_wire()
    assert wire["entry_anchor"] == "vwap"
    assert wire["exit_anchor"] == "open"
    assert "entry" not in wire and "exit" not in wire


def test_costs_to_wire_defaults() -> None:
    assert Costs().to_wire() == {
        "slippage_bps": 0.0,
        "fee_pct": 0.0,
        "vol_scaled_slippage": False,
        "vol_slippage_lookback": 20,
    }
    assert Costs(slippage_bps=2.5, fee_pct=0.001).to_wire()["slippage_bps"] == 2.5


def test_risk_to_wire_omits_none_and_renames() -> None:
    # All-default: only the always-present reentry/cooldown keys.
    assert Risk().to_wire() == {
        "stop_reentry": "immediate",
        "stop_cooldown_bars": 0,
    }
    # Renames: stop->stop_type, value->stop_value, atr_period->stop_atr_period,
    # max_drawdown->max_drawdown_limit, reentry->stop_reentry,
    # cooldown_bars->stop_cooldown_bars.
    wire = Risk(
        stop="atr", value=2.5, atr_period=14,
        reentry="cooldown", cooldown_bars=5, max_drawdown=0.25,
    ).to_wire()
    assert wire == {
        "stop_reentry": "cooldown",
        "stop_cooldown_bars": 5,
        "stop_type": "atr",
        "stop_value": 2.5,
        "stop_atr_period": 14,
        "max_drawdown_limit": 0.25,
    }


def test_sizing_to_wire_omits_none_and_renames() -> None:
    # weight -> position_weight; vol_target / leverage_limit omitted when None.
    assert Sizing().to_wire() == {
        "position_weight": 1.0,
        "vol_target_lookback": 20,
    }
    wire = Sizing(weight=0.5, vol_target=0.15, leverage_limit=2.0).to_wire()
    assert wire["position_weight"] == 0.5
    assert wire["vol_target"] == 0.15
    assert wire["leverage_limit"] == 2.0


def test_market_hours_to_wire_omits_none() -> None:
    assert MarketHours().to_wire() == {"strict_anchors": False}
    wire = MarketHours(open_hour=9.5, close_hour=16.0, strict_anchors=True).to_wire()
    assert wire == {"strict_anchors": True, "open_hour": 9.5, "close_hour": 16.0}


def test_settings_to_wire_defaults() -> None:
    assert Settings().to_wire() == {
        "risk_free_rate": 0.0,
        "random_seed": 42,
        "on_bad_data": "raise",
    }
    assert Settings(risk_free_rate=0.04, on_bad_data="zero").to_wire() == {
        "risk_free_rate": 0.04,
        "random_seed": 42,
        "on_bad_data": "zero",
    }


# ---------------------------------------------------------------------------
# Strategy.indicator() descriptor
# ---------------------------------------------------------------------------


def test_indicator_descriptor_defaults() -> None:
    # ref defaults to name; kind defaults to technical; upstream defaults to [].
    assert Strategy.indicator("rsi", period=14) == {
        "ref": "rsi",
        "name": "rsi",
        "kind": "technical",
        "params": {"period": 14},
        "upstream": [],
    }


def test_indicator_descriptor_explicit_ref_kind_upstream() -> None:
    desc = Strategy.indicator(
        "cross_above", ref="x_above", kind="transform",
        upstream=["sma_10", "sma_50"],
    )
    assert desc["ref"] == "x_above"
    assert desc["kind"] == "transform"
    assert desc["upstream"] == ["sma_10", "sma_50"]
    assert desc["params"] == {}


# ---------------------------------------------------------------------------
# Strategy.to_wire()
# ---------------------------------------------------------------------------


def test_strategy_to_wire_leaf_nodes_and_none_slots() -> None:
    ind = [Strategy.indicator("rsi", ref="rsi_14", period=14)]
    wire = Strategy(
        name="s", long_entry="rsi_14 < 30", long_exit="rsi_14 > 70",
        indicators=ind,
    ).to_wire()
    tree = wire["condition_tree"]
    assert tree["long_entry"] == {"op": "leaf", "expr": "rsi_14 < 30"}
    assert tree["long_exit"] == {"op": "leaf", "expr": "rsi_14 > 70"}
    # Unset slots serialise to None, not a leaf node.
    assert tree["short_entry"] is None
    assert tree["short_exit"] is None
    assert wire["indicators"] == ind


# ---------------------------------------------------------------------------
# Pre-built templates
# ---------------------------------------------------------------------------


def test_rsi_threshold_long_template() -> None:
    s = Strategy.rsi_threshold_long()
    assert s.name == "rsi_threshold_long"
    assert s.long_entry == "rsi_14 < 30"
    assert s.long_exit == "rsi_14 > 70"
    assert s.indicators == [Strategy.indicator("rsi", ref="rsi_14", period=14)]


def test_rsi_mean_reversion_template() -> None:
    s = Strategy.rsi_mean_reversion()
    assert s.name == "rsi_mean_reversion"
    assert s.long_entry == "(rsi_14 > 30) & (rsi_14 < 50)"
    assert s.long_exit == "rsi_14 >= 50"


def test_ma_crossover_template() -> None:
    s = Strategy.ma_crossover()
    assert s.name == "ma_crossover"
    assert s.long_entry == "x_above"
    assert s.long_exit == "x_below"
    refs = [i["ref"] for i in s.indicators]
    assert refs == ["sma_10", "sma_50", "x_above", "x_below"]
    transforms = [i for i in s.indicators if i["kind"] == "transform"]
    assert {t["ref"] for t in transforms} == {"x_above", "x_below"}
    assert transforms[0]["upstream"] == ["sma_10", "sma_50"]


def test_momentum_6m_long_template() -> None:
    s = Strategy.momentum_6m_long()
    assert s.name == "momentum_6m_long"
    assert s.long_entry == "roc_126 > 0"
    assert s.long_exit == "roc_126 <= 0"
    assert s.indicators == [Strategy.indicator("roc", ref="roc_126", period=126)]


def test_templates_return_fresh_instances() -> None:
    # Each call yields a new instance — no shared mutable state.
    a = Strategy.rsi_threshold_long()
    b = Strategy.rsi_threshold_long()
    assert a is not b
    a.indicators.append(Strategy.indicator("sma", period=5))
    assert len(b.indicators) == 1
