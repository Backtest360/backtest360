"""Error handling — catching Backtest360Error and branching on .status.

Demonstrates how to catch Backtest360Error and respond to each common case.
To trigger a real error deterministically, this example sends a deliberately
invalid API key, so the engine rejects the request with HTTP 401. In your own
code, read the key from the BACKTEST360_API_KEY environment variable instead.
"""

import pandas as pd

from backtest360 import Backtest360Error, Client, Strategy


def run_with_error_handling():
    # Deliberately invalid key so the call below raises a 401 and we can show
    # the error-handling flow. Real code: Client() reads BACKTEST360_API_KEY.
    client = Client(api_key="b360_invalid_key_for_demo")

    df = pd.DataFrame({
        "open":  [100.0, 101.0, 102.0],
        "high":  [102.0, 103.0, 104.0],
        "low":   [99.0,  100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
    }, index=pd.date_range("2020-01-01", periods=3, freq="D"))

    try:
        result = client.backtest(Strategy.rsi_threshold_long(), df)
        print("Success! Sharpe:", result.stats.get("sharpe"))

    except Backtest360Error as e:
        if e.status == 401:
            print("Invalid or expired API key.")
            print("Renew at: https://backtest360.com")

        elif e.status == 403:
            print("Your key lacks the required scope for this endpoint.")

        elif e.status == 422:
            print("Strategy or config is invalid.")
            if isinstance(e.body, dict):
                print("Details:", e.body.get("detail"))

        elif e.status in (429, 503):
            # Rate limited / quota exceeded / engine at capacity. The
            # Retry-After header (seconds) is exposed as e.retry_after.
            wait = e.retry_after or 5
            print(f"Busy ({e.status}). Retry in {wait:.0f}s.")

        elif e.status == 504:
            print("Run exceeded the engine's compute time limit. "
                  "Reduce the date range or strategy complexity.")

        elif e.status and e.status >= 500:
            print(f"Engine error ({e.status}). Request ID: {e.request_id}")
            raise  # unexpected — let it propagate

        else:
            raise  # unknown status — re-raise


if __name__ == "__main__":
    run_with_error_handling()
