"""Introspection — discover engine version, your key, indicators, templates, validate.

Demonstrates: querying the engine for its version and capabilities before
running anything, inspecting your API key's scopes and limits, listing the
predesigned strategy templates, and using client.validate_strategy() to check
that a strategy is valid without running a full backtest.

No market data required — validation works on the strategy definition alone.
"""

import os

from backtest360 import Client, Strategy

client = Client(api_key=os.environ["BACKTEST360_API_KEY"])

# ---------------------------------------------------------------------------
# Engine version + compatibility
# ---------------------------------------------------------------------------

info = client.version()
print("Engine version:", info.get("version"))
print("API contract:  ", info.get("api_contract"))

# The engine declares the client contract version it expects. The client sends
# its contract version automatically on every request (X-Client-Contract), so
# this is informational — a mismatch surfaces as an HTTP 409 error.
expected_contract = info.get("expected_client_contract")
if expected_contract:
    print(f"Expected client contract: {expected_contract}")

# ---------------------------------------------------------------------------
# Your API key — scopes, limits, usage
# ---------------------------------------------------------------------------

me = client.me()
print("\nKey scopes:", me.get("scopes"))
limits = me.get("limits", {})
print(f"Limits: {limits.get('rpm')} req/min, {limits.get('rpd')} req/day")
day = me.get("usage", {}).get("day", {})
print(f"Today: {day.get('used')} used, {day.get('remaining')} remaining")

# ---------------------------------------------------------------------------
# Predesigned strategy templates
# ---------------------------------------------------------------------------

templates = client.list_templates()
print(f"\nStrategy templates ({len(templates)}):")
for t in templates[:10]:
    print(f"  - {t.get('id')}: {t.get('description')}")

# Fetch one template's full definition — ready to validate or run.
if templates:
    full = client.list_templates(name=templates[0]["id"])
    print(f"\nFull definition of '{full.get('id')}':")
    print("  indicators:", [i.get("ref") for i in full.get("indicators", [])])

# ---------------------------------------------------------------------------
# Available indicators
# ---------------------------------------------------------------------------

indicators = client.list_indicators()
print(f"\nAvailable indicators ({len(indicators)}):")
for ind in indicators[:10]:
    print(f"  - {ind.get('name')}")
if len(indicators) > 10:
    print(f"  ... and {len(indicators) - 10} more")

# ---------------------------------------------------------------------------
# Validate a strategy — valid case
# ---------------------------------------------------------------------------

valid = client.validate_strategy(Strategy.rsi_threshold_long())
print("\nValid strategy:")
print("  valid:", valid.get("valid"))
print("  warmup_bars:", valid.get("warmup_bars"))
print("  referenced_indicators:", valid.get("referenced_indicators"))

# ---------------------------------------------------------------------------
# Validate a strategy — deliberately invalid case
# ---------------------------------------------------------------------------

# This strategy references "rsi_14" in its expressions but never declares the
# indicator, so the engine reports an unknown column reference.
broken = Strategy(
    name="missing_indicator",
    long_entry="rsi_14 < 30",
    long_exit="rsi_14 > 70",
    indicators=[],
)

invalid = client.validate_strategy(broken)
print("\nInvalid strategy:")
print("  valid:", invalid.get("valid"))
for err in invalid.get("errors", []):
    print(f"  error: [{err.get('code')}] {err.get('message')} at {err.get('location')}")
