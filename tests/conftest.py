"""Shared fixtures for the smoke-test suite.

The fixtures here replace ``httpx.Client`` with a recording fake so tests can
drive the client end-to-end without any network access. The fake mimics exactly
the usage pattern in ``Client._request``: it is a context manager and exposes
``get(url, headers=...)`` and ``post(url, headers=..., content=...)``.
"""

from __future__ import annotations

import httpx
import pandas as pd
import pytest

from backtest360 import Client

_DUMMY_KEY = "b360_test_key"
_DUMMY_URL = "https://engine.example.test"


class RecordingTransport:
    """Fake ``httpx.Client`` that returns queued responses and records calls.

    Loaded with a queue of ``httpx.Response`` objects; each request pops the
    next one. Every outgoing call is recorded as a dict with the method, path,
    JSON-decoded body, and headers so tests can assert on the wire format.
    """

    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self.calls: list[dict] = []

    def queue(self, response: httpx.Response) -> None:
        """Add a response to the queue served to subsequent requests."""
        self.responses.append(response)

    def _next(self) -> httpx.Response:
        if not self.responses:
            raise AssertionError("RecordingTransport received an unexpected request.")
        return self.responses.pop(0)

    def __enter__(self) -> RecordingTransport:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get(self, url: str, headers: dict | None = None) -> httpx.Response:
        self._record("GET", url, headers, None)
        return self._next()

    def post(
        self, url: str, headers: dict | None = None, content: str | bytes | None = None
    ) -> httpx.Response:
        self._record("POST", url, headers, content)
        return self._next()

    def _record(
        self, method: str, url: str, headers: dict | None, content: str | bytes | None
    ) -> None:
        import json
        from urllib.parse import urlsplit

        body = None
        if content is not None:
            body = json.loads(content)
        split = urlsplit(url)
        self.calls.append(
            {
                "method": method,
                "path": split.path,
                "query": split.query,
                "json": body,
                "headers": dict(headers or {}),
            }
        )


def make_response(
    status_code: int,
    *,
    json: dict | list | None = None,
    text: str | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """Build an ``httpx.Response`` with a request attached so ``.json()`` works."""
    request = httpx.Request("POST", "https://engine.example.test/api/backtest")
    if text is not None:
        return httpx.Response(status_code, text=text, headers=headers, request=request)
    return httpx.Response(status_code, json=json, headers=headers, request=request)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove client environment variables so tests are hermetic."""
    monkeypatch.delenv("BACKTEST360_API_KEY", raising=False)
    monkeypatch.delenv("BACKTEST360_ENGINE_URL", raising=False)


@pytest.fixture
def mock_engine(monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    """Patch ``httpx.Client`` with a recording fake and return it.

    Patches the name in the client module so ``Client._request`` constructs the
    fake instead of a real client. Tests queue responses on the returned object
    and inspect ``.calls`` to assert on the wire format.
    """
    transport = RecordingTransport()
    monkeypatch.setattr(
        "backtest360.client.httpx.Client", lambda *a, **k: transport
    )
    return transport


@pytest.fixture
def client() -> Client:
    """A Client wired with a dummy key and a fixed base URL."""
    return Client(api_key=_DUMMY_KEY, base_url=_DUMMY_URL)


def tiny_ohlcv() -> pd.DataFrame:
    """Return a small valid OHLCV DataFrame indexed by datetime."""
    index = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
        },
        index=index,
    )


@pytest.fixture(name="tiny_ohlcv")
def tiny_ohlcv_fixture() -> pd.DataFrame:
    """Fixture form of :func:`tiny_ohlcv`."""
    return tiny_ohlcv()
