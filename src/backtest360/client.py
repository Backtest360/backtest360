"""Backtest360 client — HTTP client and core types."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from backtest360.strategy import (
        Costs,
        Execution,
        MarketHours,
        Risk,
        Settings,
        Sizing,
        Strategy,
    )

_DEFAULT_BASE_URL = "https://api.backtest360.com"
_TIMEOUT_SECONDS = 300.0

# API contract version this client targets. Sent on every request via the
# X-Client-Contract header; when the engine drops support for this contract
# it rejects the call with HTTP 409, signalling that the client needs updating.
_CLIENT_CONTRACT = "1"

# Request bounds enforced by the engine (HTTP 422 on violation). Checked
# client-side before serialisation so oversized inputs fail fast with a clear
# client error instead of a round-trip.
_MAX_SERIES_LENGTH = 1_000_000
_MAX_INDICATORS = 128
_MAX_EXPR_LENGTH = 512

# Explicit allowlist of the public API paths the client is permitted to call.
# Any path not in this set is rejected client-side before it reaches the wire,
# so the client only ever calls the documented public endpoints listed below.
_ALLOWED_PATHS: frozenset[str] = frozenset({
    "/api/version",
    "/api/indicators",
    "/api/strategies",
    "/api/me",
    "/api/validate-strategy",
    "/api/backtest",
    "/api/latest-signal",
    "/api/data/samples",
    "/api/data/sample",
})

# Fields kept in the compact view of a strategy template returned by
# ``Client.list_templates(compact=True)`` — enough to browse and pick a
# template without pulling every template's full definition.
_TEMPLATE_COMPACT_FIELDS = ("id", "origin", "name", "description")


# ---------------------------------------------------------------------------
# Result formatting helpers
# ---------------------------------------------------------------------------

def _fmt_pct(x: float | None) -> str:
    """Format a fraction (0.089) as a percentage string ('8.9%'), or 'n/a'.

    Non-numeric, missing, or non-finite (NaN/inf) values render as ``'n/a'``
    rather than raising. ``bool`` is explicitly excluded because it is a
    subclass of ``int`` but should not render as a numeric percentage.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "n/a"
    return f"{x * 100:.1f}%"


def _fmt_ratio(x: float | None) -> str:
    """Format a ratio (1.42) to two decimal places, or 'n/a'.

    Non-numeric, missing, or non-finite (NaN/inf) values render as ``'n/a'``
    rather than raising. ``bool`` is explicitly excluded because it is a
    subclass of ``int`` but should not render as a numeric ratio.
    """
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "n/a"
    return f"{x:.2f}"


def _client_version() -> str:
    try:
        return version("backtest360")
    except PackageNotFoundError:
        return "0.0.0.dev"


def _series(values: list[Any], dates: list[Any], name: str) -> pd.Series:
    """Build a datetime-indexed Series from the engine's parallel arrays.

    Raises ``Backtest360Error`` (``CLIENT_MALFORMED_RESPONSE``) when the engine
    returns mismatched ``dates``/``values`` lengths or dates that cannot be
    parsed, instead of surfacing a raw pandas error.
    """
    if len(values) != len(dates):
        raise Backtest360Error(
            f"Engine response malformed: '{name}' has {len(values)} values "
            f"but {len(dates)} dates.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )
    try:
        index = pd.to_datetime(dates)
    except (ValueError, TypeError) as exc:
        raise Backtest360Error(
            f"Engine response malformed: '{name}' has unparseable dates.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        ) from exc
    return pd.Series(values, index=index, name=name)


# ---------------------------------------------------------------------------


class Backtest360Error(Exception):
    """Raised on any non-2xx response from the Backtest360 API.

    Args:
        message: Human-readable description of the error.
        status: HTTP status code returned by the engine (0 for client-side errors
            raised before any request is sent, e.g. invalid inputs or missing config).
        code: Machine-readable error code (e.g. ``CLIENT_NO_API_KEY``).
        body: Parsed response body (dict) or raw text if JSON parsing failed.
        request_id: Value of the ``X-Request-ID`` response header, if present.
            Quote it when reporting server errors — it joins the failure to
            the engine's logs.
        retry_after: Seconds to wait before retrying, parsed from the
            ``Retry-After`` response header. Set on capacity responses —
            429 (quota or concurrency exhausted) and 503 (engine at capacity,
            retry with backoff). ``None`` when the header is absent. A
            504 means the run exceeded the engine's compute time limit:
            reduce the date range or strategy complexity rather than retrying.

    Example:
        >>> import time
        >>> try:
        ...     result = client.backtest(strategy, df)
        ... except Backtest360Error as e:
        ...     if e.status == 401:
        ...         print("Invalid API key")
        ...     elif e.status in (429, 503) and e.retry_after:
        ...         time.sleep(e.retry_after)  # then retry
        ...     else:
        ...         raise
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        body: dict[str, Any] | str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body
        self.request_id = request_id
        self.retry_after = retry_after


# ---------------------------------------------------------------------------


class Result:
    """Wraps a ``/api/backtest`` response.

    All properties are derived lazily from the raw response dict.

    Attributes:
        stats: The full statistic set. Keyed by stable snake_case metric id
            (e.g. ``"sharpe"``) by default, or by legacy display label (e.g.
            ``"Sharpe"``) when the request was made with
            ``stats_keys="labels"``. See the metrics catalog on
            ``GET /api/sections`` for the full id/label/description mapping.
        relative: Benchmark-relative metrics (Alpha, Beta, Information Ratio,
            Tracking Error, Up/Down Capture, Capture Ratio), keyed the same way
            as ``stats``. Empty dict when no benchmark was supplied.
        trades: List of trade dicts, each with ``entry_date``, ``exit_date``,
            ``direction``, ``return_net``, etc.
        strategy_equity: Strategy equity curve as a ``pd.Series`` indexed by datetime.
        benchmark_equity: Benchmark equity curve as a ``pd.Series`` indexed by datetime.
            Empty Series when no benchmark was supplied in the request.
        returns: Net-of-cost log-return series indexed by datetime.
        signals: Signal series (``{-1, 0, 1}``) indexed by datetime.
        markers: Warmup and trade-boundary markers for chart annotation.
        data_quality: The engine's assessment of the input data quality.
        raw: The full ``result`` dict — everything the engine returned.

    Example:
        >>> result = client.backtest(strategy, df, benchmark=benchmark_df)
        >>> result.summary()
        Performance Summary
        ─────────────────────────────
        Total Return           8.3%
        Annualized Return     12.1%
        Annualized Std Dev    18.4%
        Sharpe Ratio           1.42
        >>> ax = result.strategy_equity.plot(title="Strategy vs Benchmark", label="Strategy")
        >>> result.benchmark_equity.plot(ax=ax, label="Benchmark", linestyle="--")
        >>> for trade in result.trades[:5]:
        ...     print(trade["entry_date"], trade["return_net"])
    """

    def __init__(self, data: dict[str, Any], relative: dict[str, Any] | None = None) -> None:
        self._data = data
        self._relative = relative or {}

    @property
    def stats(self) -> dict[str, Any]:
        """Performance statistics dict (120+ metrics).

        Keyed by stable snake_case metric id by default (e.g. ``"sharpe"``);
        see the ``stats_keys`` argument on the request method that produced
        this result and the metrics catalog on ``GET /api/sections``.

        Benchmark-relative metrics (Alpha, Beta, Information Ratio, Tracking
        Error, Up/Down Capture, Capture Ratio) are NOT in this dict — see
        :attr:`relative`.
        """
        stats: dict[str, Any] = self._data.get("stats", {})
        return stats

    @property
    def relative(self) -> dict[str, Any]:
        """Benchmark-relative metrics (Alpha, Beta, Information Ratio, Tracking
        Error, Up/Down Capture, Capture Ratio), keyed the same way as ``stats``
        (per the request's ``stats_keys``). Empty dict when no benchmark was
        supplied.

        These metrics are NOT in ``stats`` — the engine returns them in a
        separate ``relative`` block, and the client surfaces them here.
        """
        return self._relative

    @property
    def trades(self) -> list[dict[str, Any]]:
        """Trade log — list of dicts with entry/exit date, direction, return."""
        trades: list[dict[str, Any]] = self._data.get("trades", [])
        return trades

    @property
    def strategy_equity(self) -> pd.Series:
        """Strategy equity curve as a ``pd.Series`` indexed by datetime.

        This series is always present when ``dates`` is non-empty. A mismatch
        in length between ``dates`` and ``strategy_equity`` is treated as a
        malformed engine response and raises ``Backtest360Error``.
        """
        series = self._data.get("series", {})
        return _series(
            series.get("strategy_equity", []), series.get("dates", []), "strategy_equity"
        )

    @property
    def benchmark_equity(self) -> pd.Series:
        """Benchmark equity curve as a ``pd.Series`` indexed by datetime.

        Empty Series when no benchmark was supplied in the request.
        Unlike ``strategy_equity``, ``returns``, and ``signals``, this array is
        optional — the engine omits it when no benchmark is configured.
        """
        series = self._data.get("series", {})
        vals = series.get("benchmark_equity")
        # Intentionally returns an empty Series rather than raising: benchmark is
        # optional and the engine legitimately omits it from the response.
        if not vals:
            return pd.Series([], dtype=float, name="benchmark_equity")
        return _series(vals, series.get("dates", []), "benchmark_equity")

    @property
    def returns(self) -> pd.Series:
        """Net-of-cost log-return series indexed by datetime.

        Always present when ``dates`` is non-empty. A length mismatch raises
        ``Backtest360Error`` (``CLIENT_MALFORMED_RESPONSE``).
        """
        series = self._data.get("series", {})
        return _series(series.get("returns", []), series.get("dates", []), "returns")

    @property
    def signals(self) -> pd.Series:
        """Signal series (``{-1, 0, 1}``) indexed by datetime.

        Always present when ``dates`` is non-empty. A length mismatch raises
        ``Backtest360Error`` (``CLIENT_MALFORMED_RESPONSE``).
        """
        series = self._data.get("series", {})
        return _series(series.get("signals", []), series.get("dates", []), "signals")

    @property
    def markers(self) -> dict[str, Any]:
        """Warmup and trade-boundary markers for chart annotation.

        Dict with the warmup boundary (``warmup_bars``, ``warmup_end_index``,
        ``warmup_end_date``) and the first/last trade positions
        (``first_trade_index``, ``first_trade_date``, ``last_trade_exit_index``,
        ``last_trade_exit_date``). Individual fields are ``None`` when not
        applicable (e.g. a run with no trades). Empty dict when the engine
        response carries no markers.
        """
        markers: dict[str, Any] = self._data.get("markers") or {}
        return markers

    @property
    def data_quality(self) -> dict[str, Any]:
        """The engine's assessment of the input data quality.

        Reports issues found while preparing the run's data, such as bad
        prices, missing bars, and quality warnings. Empty dict when the
        engine response carries no data-quality block.
        """
        data_quality: dict[str, Any] = self._data.get("data_quality") or {}
        return data_quality

    def summary(self) -> None:
        """Print a high-level performance summary.

        Annualized Return, Annualized Std Dev, and Sharpe are read directly from
        the engine's ``stats`` dict (where they are already computed and annualized
        to the bar frequency used in the run). Each is read id-first with a
        display-label fallback — the stable metric ids (``cagr``, ``vol_ann``,
        ``sharpe``) when present, otherwise the legacy display labels (``CAGR``,
        ``Vol (Ann)``, ``Sharpe``) — so the summary renders correctly whether the
        stats are id-keyed or label-keyed. Total Return is derived from the equity
        curve as ``last / first - 1``.

        Missing stats render as ``n/a`` — no exception is raised.

        Example:
            >>> result.summary()
            Performance Summary
            ─────────────────────────────
            Total Return           8.3%
            Annualized Return     12.1%
            Annualized Std Dev    18.4%
            Sharpe Ratio           1.42
        """
        stats = self.stats

        # Total Return — the only value we compute client-side; needs no
        # annualization factor and is not in the documented stats key set.
        eq = self.strategy_equity
        if len(eq) >= 2 and eq.iloc[0] != 0 and pd.notna(eq.iloc[0]) and pd.notna(eq.iloc[-1]):
            total_ret: float | None = eq.iloc[-1] / eq.iloc[0] - 1
        else:
            total_ret = None

        # Read id-first with a display-label fallback so the summary renders
        # regardless of how the engine keyed the stats: the contract-3 id keys
        # win when present, otherwise the legacy display labels are used (the
        # keying returned for stats_keys="labels", and by any engine predating
        # api_contract 3, which ignores the stats_keys request field). The
        # default-arg form is key-presence based, so a legitimate 0.0 is kept
        # rather than falling through to the label lookup.
        ann_ret = stats.get("cagr", stats.get("CAGR"))
        ann_std = stats.get("vol_ann", stats.get("Vol (Ann)"))
        sharpe = stats.get("sharpe", stats.get("Sharpe"))

        width = 29
        rule = "─" * width
        label_w = 19

        lines = [
            "Performance Summary",
            rule,
            f"{'Total Return':<{label_w}}{_fmt_pct(total_ret):>10}",
            f"{'Annualized Return':<{label_w}}{_fmt_pct(ann_ret):>10}",
            f"{'Annualized Std Dev':<{label_w}}{_fmt_pct(ann_std):>10}",
            f"{'Sharpe Ratio':<{label_w}}{_fmt_ratio(sharpe):>10}",
        ]
        print("\n".join(lines))

    @property
    def raw(self) -> dict[str, Any]:
        """Full engine response dict — access any field not exposed as a property."""
        return self._data


# ---------------------------------------------------------------------------


class Client:
    """Synchronous HTTP client for the Backtest360 API.

    Args:
        api_key: Your Backtest360 API key. Falls back to the
            ``BACKTEST360_API_KEY`` environment variable. Raises
            ``Backtest360Error(code="CLIENT_NO_API_KEY")`` immediately if neither is set.
        base_url: Engine base URL. Falls back to ``BACKTEST360_ENGINE_URL`` env var,
            then ``https://api.backtest360.com``.
        timeout: Request timeout in seconds. Defaults to 300 (backtests can
            be slow).

    Example:
        >>> import yfinance as yf
        >>> from backtest360 import Client, Strategy
        >>>
        >>> df = yf.download("BTC-USD", period="1y", interval="1d",
        ...     auto_adjust=False, multi_level_index=False, progress=False)
        >>> df.columns = df.columns.str.lower()
        >>>
        >>> result = Client(api_key="b360_...").backtest(
        ...     Strategy.rsi_threshold_long(), df
        ... )
        >>> print(result.stats["sharpe"])
        >>> result.strategy_equity.plot(title="Equity curve")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        resolved_key = api_key or os.environ.get("BACKTEST360_API_KEY", "")
        if not resolved_key:
            raise Backtest360Error(
                "No API key provided. Pass api_key=... or set the "
                "BACKTEST360_API_KEY environment variable. "
                "Sign up at backtest360.com.",
                status=0,
                code="CLIENT_NO_API_KEY",
            )
        self._api_key = resolved_key
        self._base_url = (
            base_url or os.environ.get("BACKTEST360_ENGINE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout

    def _headers(self, request_id: str | None = None) -> dict[str, str]:
        headers = {
            "X-API-Key": self._api_key,
            "X-Client-Version": f"backtest360/{_client_version()}",
            "X-Client-Contract": _CLIENT_CONTRACT,
            "Content-Type": "application/json",
        }
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an HTTP request and return the parsed JSON response.

        Raises:
            Backtest360Error: On any non-2xx response or a forbidden path.
        """
        if path not in _ALLOWED_PATHS:
            raise Backtest360Error(
                f"Path '{path}' is not permitted. The client is restricted to: "
                + ", ".join(sorted(_ALLOWED_PATHS)),
                status=0,
                code="CLIENT_PATH_FORBIDDEN",
            )
        url = f"{self._base_url}{path}"
        # Query params are encoded onto the URL (rather than passed as httpx's
        # `params=`) so the allowlist check above stays on the bare path.
        if params:
            from urllib.parse import urlencode

            query = urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"
        try:
            payload_bytes: str = json.dumps(body or {}, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise Backtest360Error(
                f"Payload is not JSON-serializable: {exc}. "
                "Check for NaN, Inf, or non-serializable types in the request body.",
                status=0,
                code="CLIENT_INVALID_PAYLOAD",
            ) from exc
        with httpx.Client(timeout=self._timeout) as http:
            if method == "GET":
                response = http.get(url, headers=self._headers(request_id))
            else:
                response = http.post(
                    url,
                    headers=self._headers(request_id),
                    content=payload_bytes,
                )

        if response.status_code >= 400:
            try:
                resp_body: dict[str, Any] | str | None = response.json()
            except Exception:
                resp_body = response.text or None

            echoed_request_id = response.headers.get("x-request-id")
            retry_after_header = response.headers.get("retry-after")
            try:
                retry_after = float(retry_after_header) if retry_after_header else None
            except ValueError:
                retry_after = None

            error_code: str | None = None
            if isinstance(resp_body, dict):
                detail = resp_body.get("detail")
                if isinstance(detail, str):
                    message = detail
                elif isinstance(detail, dict):
                    message = detail.get("message", "") or response.text
                    error_code = detail.get("code")
                elif isinstance(detail, list):
                    # The API returns 422 validation errors as a list of per-field error dicts.
                    parts = []
                    for item in detail:
                        if isinstance(item, dict):
                            loc = " -> ".join(str(p) for p in item.get("loc", []))
                            msg = item.get("msg", "")
                            parts.append(f"{loc}: {msg}" if loc else msg)
                        else:
                            parts.append(str(item))
                    message = "; ".join(filter(None, parts)) or response.text
                elif detail is None:
                    # No 'detail' key — try common top-level error fields.
                    message = (
                        resp_body.get("error")
                        or resp_body.get("message")
                        or response.text
                    )
                else:
                    message = response.text
            else:
                message = str(resp_body) if resp_body else ""

            raise Backtest360Error(
                message or f"HTTP {response.status_code}",
                status=response.status_code,
                code=error_code,
                body=resp_body,
                request_id=echoed_request_id,
                retry_after=retry_after,
            )

        try:
            return response.json()
        except Exception:
            raise Backtest360Error(
                f"Server returned a non-JSON response (HTTP {response.status_code}).",
                status=response.status_code,
                code="CLIENT_MALFORMED_RESPONSE",
                body=response.text or None,
            ) from None

    # ---------------------------------------------------------------------------
    # Public API methods
    # ---------------------------------------------------------------------------

    def version(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Return engine version info from ``GET /api/version``.

        Args:
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header (letters, digits, ``.``, ``_``, ``-``; max 64 chars).
                Echoed on the response and recorded in the engine's logs;
                generated server-side when omitted.

        Returns:
            Dict with at minimum ``{"version": "x.y.z", "engine": "...", "api_contract": "..."}``.
            The response also includes ``expected_client_contract`` — the contract
            version the engine expects clients to declare. The client sends its own
            contract version automatically on every request via the
            ``X-Client-Contract`` header.

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> info = client.version()
            >>> print(info["version"])
            0.5.3
        """
        resp = self._request("GET", "/api/version", request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return resp

    def me(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Introspect the calling API key via ``GET /api/me``.

        Returns the key's granted scopes, numeric limits, current usage against
        those limits, and capability flags — so you can discover what the key
        can do up front instead of learning it from 401/403/429 errors.
        Requires only a valid key; no particular scope.

        Args:
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            Dict with:

            - ``scopes``: list of granted scope strings (sorted).
            - ``limits``: per-key numeric limits — ``rpm``, ``rpd``,
                ``max_concurrent``, ``max_bars_per_run`` (the last is ``None``
                when the key has no bar cap).
            - ``usage``: point-in-time usage including this request —
                ``minute`` / ``day`` (each ``used`` / ``remaining`` /
                ``resets_in_seconds``) and ``concurrent`` (``used`` / ``remaining``).
            - ``capabilities``: boolean flags such as ``server_side_fetch`` and
                ``full_metrics``.

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            ```pycon
            >>> info = client.me()
            >>> print(info["scopes"])
            ['backtest.run', 'meta.read', 'strategy.validate']
            >>> print(info["limits"]["rpm"], info["usage"]["minute"]["remaining"])
            ```
        """
        resp = self._request("GET", "/api/me", request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return resp

    def list_indicators(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        """Return the engine's indicator library from ``GET /api/indicators``.

        Each entry describes an indicator's name, parameters, kind, and output
        columns. Use this to discover available indicators and their parameter
        schemas when building custom strategies.

        See also: https://api.backtest360.com/docs

        Args:
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            List of indicator descriptor dicts.

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> for ind in client.list_indicators():
            ...     print(ind["name"], ind.get("params", {}).keys())
        """
        resp = self._request("GET", "/api/indicators", request_id=request_id)
        if isinstance(resp, list):
            indicators: list[dict[str, Any]] = resp
            return indicators
        if isinstance(resp, dict):
            from_key: list[dict[str, Any]] = resp.get("indicators", [])
            return from_key
        raise Backtest360Error(
            f"Expected a JSON array or object from the engine; got {type(resp).__name__}.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )

    def list_templates(
        self,
        name: str | None = None,
        compact: bool = True,
        *,
        collection: str | None = None,
        q: str | None = None,
        tags: str | Iterable[str] | None = None,
        detail: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        raw: bool = False,
        request_id: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return the predesigned strategy templates from ``GET /api/strategies``.

        The engine serves code-shipped, versioned strategy definitions,
        organized into named collections. The response is tier-filtered
        server-side by the calling key's scopes: non-admin keys see only
        public templates. Each template is a ready-to-run strategy in the
        canonical shape a backtest accepts (``condition_tree`` + ``indicators``)
        plus parameter metadata (``requires`` / ``defaults`` / ``locked_params``)
        and ``origin`` / ``collection`` / ``tags`` fields.

        With no arguments, returns a compact catalog — a list of dicts carrying
        only ``id``, ``origin``, ``name``, and ``description`` — for the
        engine's default collection (currently the curated set). Pass
        ``compact=False`` for the full definition of every template. Pass
        ``name`` (an ``id`` or ``name``, matched case-insensitively) to return
        that single template's full entry as a dict; ``compact`` is ignored in
        that case.

        Use ``collection``, ``q``, and ``tags`` to filter which templates come
        back, and ``limit`` / ``offset`` to page through a large result set.

        Args:
            name: Optional ``id`` or ``name`` of one template to fetch in full,
                matched case-insensitively. When given, the return value is a
                single template dict rather than a list.
            compact: When ``True`` (the default) and ``name`` is not given,
                each returned entry carries only the compact discovery fields.
                When ``False``, each entry is the template's full definition.
            collection: Which collection to browse — a collection name, or
                ``"all"`` to search across every collection. Left unset by
                default, which leaves the engine's own default collection in
                effect.
            q: Free-text search over template name, description, and tags.
                Unset by default (no search filter applied).
            tags: One tag, or an iterable of tags, to filter by. An iterable
                is joined into the comma-separated list the engine expects.
                Unset by default (no tag filter applied).
            detail: ``"full"`` or ``"compact"`` — asks the engine itself to
                include full or compact fields per entry on the wire. This is
                independent of the ``compact`` argument above, which trims
                fields on the client side after the response arrives; pass
                ``detail="compact"`` to also reduce what the server sends.
                Unset by default.
            limit: Maximum number of templates to return (the engine accepts
                1-500). Unset by default (the engine's own default applies).
            offset: Number of matching templates to skip, for paging through
                results beyond ``limit``. Unset by default.
            raw: When ``True`` (and ``name`` is not given), return the
                engine's full response envelope instead of just the entry
                list — including ``count``, ``total``, and ``next_offset``,
                which you need to page through a filtered or large catalog.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            A list of template dicts; the raw response envelope dict when
            ``raw=True``; or — when ``name`` is given — the single matching
            template dict.

        Raises:
            Backtest360Error: On any non-2xx response, or, when ``name`` is
                given and no template matches, with
                ``code="CLIENT_TEMPLATE_NOT_FOUND"``.

        Example:
            >>> for t in client.list_templates():
            ...     print(t["id"], "-", t["description"])
            >>> strat = client.list_templates(name="rsi_mean_reversion")
            >>> page = client.list_templates(
            ...     collection="all", tags=["momentum"], limit=20, raw=True
            ... )
            >>> print(page["total"], page["next_offset"])
            >>> result = client.backtest_raw({"strategy": strat, ...})
        """
        tags_param: str | None
        if tags is None or isinstance(tags, str):
            tags_param = tags
        else:
            tags_param = ",".join(tags)
        resp = self._request(
            "GET",
            "/api/strategies",
            request_id=request_id,
            params={
                "collection": collection,
                "q": q,
                "tags": tags_param,
                "detail": detail,
                "limit": limit,
                "offset": offset,
            },
        )
        if isinstance(resp, dict):
            entries = resp.get("strategies")
        elif isinstance(resp, list):
            entries = resp
        else:
            entries = None
        if not isinstance(entries, list):
            raise Backtest360Error(
                "Engine response malformed: '/api/strategies' did not return a "
                "'strategies' list.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )

        if name is not None:
            wanted = name.lower()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if (
                    str(entry.get("id", "")).lower() == wanted
                    or str(entry.get("name", "")).lower() == wanted
                ):
                    return entry
            raise Backtest360Error(
                f"No template named '{name}'. Call list_templates() without a "
                "name to see what is available.",
                status=0,
                code="CLIENT_TEMPLATE_NOT_FOUND",
            )

        if raw:
            return resp

        if compact:
            return [
                {k: e[k] for k in _TEMPLATE_COMPACT_FIELDS if k in e}
                for e in entries
                if isinstance(e, dict)
            ]
        return [e for e in entries if isinstance(e, dict)]

    def sample_symbols(self, *, request_id: str | None = None) -> list[str]:
        """Return the symbols available as bundled sample data.

        Uses ``GET /api/data/samples``. The engine serves a small set of
        ready-made daily OHLCV datasets so examples and quick experiments run
        without an external data feed. Free to call — no paid data access
        required.

        Args:
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            List of symbol strings (e.g. ``["SPY", "QQQ", "BTC"]``).

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> client.sample_symbols()
            ['SPY', 'QQQ', 'BTC']
        """
        resp = self._request("GET", "/api/data/samples", request_id=request_id)
        if isinstance(resp, dict):
            symbols = resp.get("symbols")
            if isinstance(symbols, list):
                return symbols
        raise Backtest360Error(
            "Engine response malformed: '/api/data/samples' did not return a "
            "'symbols' list.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )

    def sample_data(
        self, symbol: str = "SPY", *, request_id: str | None = None
    ) -> pd.DataFrame:
        """Return a bundled sample OHLCV dataset as a ready-to-backtest DataFrame.

        Uses ``GET /api/data/sample``. The returned frame has a
        ``DatetimeIndex`` and lowercase ``open/high/low/close/volume`` columns —
        the exact shape :meth:`backtest`, :meth:`backtest_signals`, and
        :meth:`latest_signal` expect, so it is a drop-in for those methods.
        Free to call — no paid data access required.

        Args:
            symbol: One of the symbols returned by :meth:`sample_symbols`
                (default ``"SPY"``). Unknown symbols raise ``Backtest360Error``
                with ``code="INVALID_SYMBOL"``.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            A ``pd.DataFrame`` indexed by datetime with lowercase OHLCV columns.

        Raises:
            Backtest360Error: On any non-2xx response (including an unknown
                symbol).

        Example:
            >>> df = client.sample_data("BTC")
            >>> result = client.backtest(Strategy.rsi_threshold_long(), df)
        """
        resp = self._request(
            "GET", "/api/data/sample", request_id=request_id, params={"symbol": symbol}
        )
        if not isinstance(resp, dict) or not isinstance(resp.get("ohlcv"), dict):
            raise Backtest360Error(
                "Engine response malformed: '/api/data/sample' did not return an "
                "'ohlcv' block.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return _ohlcv_from_wire(resp["ohlcv"])

    def validate_strategy(
        self, strategy: Strategy, *, request_id: str | None = None
    ) -> dict[str, Any]:
        """Validate a strategy without running a backtest.

        Args:
            strategy: A :class:`~backtest360.Strategy` instance to validate.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            Validation result dict. For a valid strategy: ``valid`` (True),
            ``warmup_bars``, ``referenced_indicators``, and related fields.
            For an invalid strategy: ``valid`` (False) and ``errors``, a list
            of dicts with ``code``, ``location``, ``message``, and ``context``.

        Raises:
            Backtest360Error: On HTTP errors other than a validation failure.

        Example:
            >>> v = client.validate_strategy(Strategy.rsi_threshold_long())
            >>> print(v["valid"], v.get("errors", []))
        """
        _check_strategy_bounds(strategy)
        try:
            resp = self._request(
                "POST",
                "/api/validate-strategy",
                {"strategy": strategy.to_wire()},
                request_id=request_id,
            )
        except Backtest360Error as exc:
            # The engine reports a failed validation as a 422 whose body is the
            # validation result itself — return it like any other outcome.
            if exc.status == 422 and isinstance(exc.body, dict) and "valid" in exc.body:
                return exc.body
            raise
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return resp

    def latest_signal(
        self,
        strategy: Strategy,
        ohlcv: pd.DataFrame,
        *,
        execution: Execution | None = None,
        costs: Costs | None = None,
        risk: Risk | None = None,
        sizing: Sizing | None = None,
        market_hours: MarketHours | None = None,
        settings: Settings | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the latest signal for a strategy on the given data.

        Uses ``POST /api/latest-signal``. Returns only the most-recent bar's
        signal and per-condition diagnostics — no P&L or statistics.

        Args:
            strategy: Strategy definition.
            ohlcv: DataFrame indexed by datetime with columns
                ``open/high/low/close/volume``.
            execution: Execution configuration (optional).
            costs: Cost configuration (optional).
            risk: Risk / stop configuration (optional).
            sizing: Position sizing configuration (optional).
            market_hours: Daily anchor-hour configuration (optional).
            settings: Engine-level run settings (optional).
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            Dict with ``signal`` (int), ``long_entry_fired`` (bool), and
            related diagnostic fields.

        Raises:
            Backtest360Error: On HTTP error or invalid strategy.

        Example:
            >>> sig = client.latest_signal(Strategy.rsi_threshold_long(), df)
            >>> print(sig["signal"])  # -1 / 0 / 1
        """
        body = _build_backtest_body(
            strategy=strategy,
            ohlcv=ohlcv,
            benchmark=None,
            execution=execution,
            costs=costs,
            risk=risk,
            sizing=sizing,
            market_hours=market_hours,
            settings=settings,
            stats_keys=None,
        )
        resp = self._request("POST", "/api/latest-signal", body, request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        result: dict[str, Any] = resp.get("result", resp)
        return result

    def backtest_raw(
        self, payload: dict[str, Any], *, request_id: str | None = None
    ) -> dict[str, Any]:
        """Send a raw ``POST /api/backtest`` payload, return the raw response dict.

        For users who want exact control over the wire format — build the JSON
        payload yourself with the API docs open. The payload is sent as-is,
        with no client-side validation and no defaults applied. The engine's
        backtest request is the leg-based ``{"run": {...}, "legs": [...]}``
        envelope: each leg needs an ``id`` and a ``data_source``, ``stats_keys``
        lives under ``run`` (set it to ``"ids"`` for stable metric-id keys), and
        a benchmark is its own ``{"benchmark": true}`` leg. See the metrics
        catalog on ``GET /api/sections`` for the id/label mapping.

        Args:
            payload: Dict matching the ``/api/backtest`` request body exactly.
                     See https://api.backtest360.com/docs for the full schema.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            The full response dict from the engine (``run`` and ``legs`` keys
            and all).

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> resp = client.backtest_raw({
            ...     "run": {"stats_keys": "ids"},
            ...     "legs": [{
            ...         "id": "strategy",
            ...         "data_source": {"ohlcv": {...}},
            ...         "strategy": {"condition_tree": {...}, "indicators": [...]},
            ...         "execution": {"signal_frequency": "daily"},
            ...     }],
            ... })
        """
        resp = self._request("POST", "/api/backtest", payload, request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return resp

    def backtest(
        self,
        strategy: Strategy,
        ohlcv: pd.DataFrame,
        *,
        benchmark: pd.DataFrame | None = None,
        execution: Execution | None = None,
        costs: Costs | None = None,
        risk: Risk | None = None,
        sizing: Sizing | None = None,
        market_hours: MarketHours | None = None,
        settings: Settings | None = None,
        stats_keys: str = "ids",
        request_id: str | None = None,
    ) -> Result:
        """Run a historical backtest and return a :class:`Result`.

        Args:
            strategy: Strategy definition. Use a template (e.g.
                ``Strategy.rsi_threshold_long()``) or build your own.
            ohlcv: DataFrame indexed by datetime with lowercase columns
                ``open``, ``high``, ``low``, ``close`` (and optionally
                ``volume``).
            benchmark: Optional benchmark DataFrame (same shape as ``ohlcv``).
                When provided, benchmark-relative metrics (Alpha, Beta, Information Ratio,
                Tracking Error, Up/Down Capture, Capture Ratio) are surfaced on ``result.relative``.
            execution: Execution timing config — ``entry``, ``exit``,
                ``signal_frequency``, etc.
            costs: Transaction costs — ``slippage_bps``, ``fee_pct``, etc.
            risk: Stop-loss / drawdown protection config.
            sizing: Position sizing config.
            market_hours: Daily anchor-hour config for sub-daily execution.
            settings: Engine-level run settings — RFR, RNG seed, bad-data
                policy.
            stats_keys: How the returned ``result.stats`` dict is keyed —
                ``"ids"`` (default) for stable snake_case metric ids (e.g.
                ``"sharpe"``), or ``"labels"`` for legacy display-label keys
                (e.g. ``"Sharpe"``). See the metrics catalog on
                ``GET /api/sections`` for the full id/label/description mapping.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            A :class:`Result` wrapping the engine response.

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> from backtest360 import Client, Strategy, Execution, Costs, Settings
            >>> result = Client(api_key="...").backtest(
            ...     Strategy.rsi_threshold_long(), df,
            ...     execution=Execution(signal_frequency="daily"),
            ...     costs=Costs(slippage_bps=2.5, fee_pct=0.001),
            ...     settings=Settings(risk_free_rate=0.04),
            ... )
            >>> print(result.stats["sharpe"])
            >>> result.strategy_equity.plot()
        """
        _check_strategy_bounds(strategy)
        exec_wire = _build_execution_wire(execution, costs, risk, sizing, market_hours, settings)
        legs = [
            _build_strategy_leg(
                leg_id=_STRATEGY_LEG_ID, ohlcv=ohlcv, strategy=strategy, exec_wire=exec_wire,
            )
        ]
        reference: str | None = None
        if benchmark is not None:
            legs.append(_build_benchmark_leg(leg_id=_BENCHMARK_LEG_ID, ohlcv=benchmark))
            reference = _BENCHMARK_LEG_ID
        body = _build_backtest_envelope(legs=legs, stats_keys=stats_keys, reference=reference)
        resp = self._request("POST", "/api/backtest", body, request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return _result_from_legs(resp)

    def backtest_signals(
        self,
        signals: pd.Series,
        ohlcv: pd.DataFrame,
        *,
        name: str | None = None,
        benchmark: pd.DataFrame | None = None,
        execution: Execution | None = None,
        costs: Costs | None = None,
        risk: Risk | None = None,
        sizing: Sizing | None = None,
        market_hours: MarketHours | None = None,
        settings: Settings | None = None,
        stats_keys: str = "ids",
        request_id: str | None = None,
    ) -> Result:
        """Run a backtest using a pre-computed signal series.

        Use this when your signal logic lives outside the engine (e.g. a
        machine-learning model, a custom indicator). Pass a ``pd.Series`` of
        ``{-1, 0, 1}`` indexed by datetime; the engine skips signal generation
        and runs execution, costing, and statistics directly on your series.

        Args:
            signals: Integer series indexed by datetime with values in
                ``{-1, 0, 1}``. ``1`` = long, ``-1`` = short, ``0`` = flat.
                Boolean series are also accepted (``True`` → 1, ``False`` → 0).
                Must cover the same date range as ``ohlcv``.
            ohlcv: DataFrame indexed by datetime with lowercase columns
                ``open``, ``high``, ``low``, ``close`` (and optionally
                ``volume``).
            name: Optional label for the strategy (used in engine output).
            benchmark: Optional benchmark DataFrame (same shape as ``ohlcv``).
            execution: Execution timing config.
            costs: Transaction costs.
            risk: Stop-loss / drawdown protection config.
            sizing: Position sizing config.
            market_hours: Daily anchor-hour config for sub-daily execution.
            settings: Engine-level run settings.
            stats_keys: How the returned ``result.stats`` dict is keyed —
                ``"ids"`` (default) for stable snake_case metric ids (e.g.
                ``"sharpe"``), or ``"labels"`` for legacy display-label keys
                (e.g. ``"Sharpe"``). See the metrics catalog on
                ``GET /api/sections`` for the full id/label/description mapping.
            request_id: Optional correlation id sent as the ``X-Request-ID``
                header. See :meth:`version`.

        Returns:
            A :class:`Result` wrapping the engine response.

        Raises:
            Backtest360Error: On any non-2xx response.

        Example:
            >>> import pandas as pd
            >>> signals = pd.Series([0, 1, 1, 0, -1, 0], index=df.index)
            >>> result = client.backtest_signals(signals, df)
            >>> print(result.stats["sharpe"])
        """
        # Validate index alignment before any wire serialisation.  The engine
        # requires signals and OHLCV to share the same date range; catching the
        # mismatch here surfaces a clear client error instead of an opaque 422.
        if not signals.index.equals(ohlcv.index):
            raise Backtest360Error(
                f"signals and ohlcv must share the same datetime index. "
                f"signals has {len(signals)} rows, ohlcv has {len(ohlcv)} rows. "
                "Align the two before calling backtest_signals().",
                status=0,
                code="CLIENT_INVALID_SIGNALS",
            )
        exec_wire = _build_execution_wire(execution, costs, risk, sizing, market_hours, settings)
        legs = [
            _build_strategy_leg(
                leg_id=_STRATEGY_LEG_ID,
                ohlcv=ohlcv,
                signals_wire=_signals_to_wire(signals, name),
                exec_wire=exec_wire,
            )
        ]
        reference: str | None = None
        if benchmark is not None:
            legs.append(_build_benchmark_leg(leg_id=_BENCHMARK_LEG_ID, ohlcv=benchmark))
            reference = _BENCHMARK_LEG_ID
        body = _build_backtest_envelope(legs=legs, stats_keys=stats_keys, reference=reference)
        resp = self._request("POST", "/api/backtest", body, request_id=request_id)
        if not isinstance(resp, dict):
            raise Backtest360Error(
                f"Expected a JSON object from the engine; got {type(resp).__name__}.",
                status=0,
                code="CLIENT_MALFORMED_RESPONSE",
            )
        return _result_from_legs(resp)


# ---------------------------------------------------------------------------
# Wire serialisation helpers
# ---------------------------------------------------------------------------


def _ohlcv_to_wire(df: pd.DataFrame) -> dict[str, Any]:
    """Serialise a DataFrame to the engine's parallel-array OHLCV format."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise Backtest360Error(
            "OHLCV DataFrame index must be a DatetimeIndex. "
            f"Got {type(df.index).__name__}. "
            "Set a datetime index before calling (e.g. df.index = pd.to_datetime(df.index)).",
            status=0,
            code="CLIENT_INVALID_OHLCV",
        )
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise Backtest360Error(
            f"OHLCV DataFrame missing required column(s): {', '.join(missing)}. "
            "Provide open/high/low/close (volume optional).",
            status=0,
            code="CLIENT_INVALID_OHLCV",
        )
    if len(df) == 0:
        raise Backtest360Error(
            "OHLCV DataFrame is empty. Provide at least one row of data.",
            status=0,
            code="CLIENT_INVALID_OHLCV",
        )
    if len(df) > _MAX_SERIES_LENGTH:
        raise Backtest360Error(
            f"OHLCV DataFrame has {len(df):,} rows, exceeding the engine's "
            f"per-series limit of {_MAX_SERIES_LENGTH:,}. Trim the date range "
            "or use a coarser bar frequency.",
            status=0,
            code="CLIENT_INVALID_OHLCV",
        )
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            continue
        series = df[col]
        invalid = bool(series.isna().any())
        if not invalid:
            kind = series.dtype.kind
            if kind in "fc":
                # No NaN present (checked above); abs of any infinity is +inf.
                invalid = bool((series.abs() == float("inf")).any())
            elif kind == "O":
                # Object-dtype columns: only float infinities are invalid.
                invalid = any(isinstance(v, float) and math.isinf(v) for v in series)
        if invalid:
            raise Backtest360Error(
                f"OHLCV column '{col}' contains NaN or infinite values. "
                "Clean the DataFrame before calling (e.g. df.dropna() or df.ffill()).",
                status=0,
                code="CLIENT_INVALID_OHLCV",
            )
    result: dict[str, Any] = {
        "dates": [ts.isoformat() for ts in df.index],
        "open":  df["open"].tolist(),
        "high":  df["high"].tolist(),
        "low":   df["low"].tolist(),
        "close": df["close"].tolist(),
    }
    if "volume" in df.columns:
        result["volume"] = df["volume"].tolist()
    return result


def _ohlcv_from_wire(ohlcv: dict[str, Any]) -> pd.DataFrame:
    """Parse the engine's parallel-array OHLCV block into a DataFrame.

    Inverse of :func:`_ohlcv_to_wire`: builds a ``DatetimeIndex``-indexed frame
    with lowercase ``open/high/low/close`` (and ``volume`` when present) — the
    shape the backtest methods consume. Reuses :func:`_series` so a length
    mismatch or unparseable date surfaces as ``CLIENT_MALFORMED_RESPONSE``.
    """
    dates = ohlcv.get("dates", [])
    columns: dict[str, pd.Series] = {}
    for col in ("open", "high", "low", "close", "volume"):
        values = ohlcv.get(col)
        if values is None:
            continue
        columns[col] = _series(values, dates, col)
    missing = [c for c in ("open", "high", "low", "close") if c not in columns]
    if missing:
        raise Backtest360Error(
            "Engine sample response is missing required column(s): "
            f"{', '.join(missing)}.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )
    # All columns share the identical DatetimeIndex built by _series, so the
    # DataFrame assembly aligns without reindexing.
    return pd.DataFrame(columns)


def _signals_to_wire(signals: pd.Series, name: str | None) -> dict[str, Any]:
    """Serialise a signal series to the engine's parallel-array wire format."""
    if not isinstance(signals.index, pd.DatetimeIndex):
        raise Backtest360Error(
            "Signals Series index must be a DatetimeIndex. "
            f"Got {type(signals.index).__name__}. "
            "Set a datetime index before calling "
            "(e.g. signals.index = pd.to_datetime(signals.index)).",
            status=0,
            code="CLIENT_INVALID_SIGNALS",
        )
    if len(signals) > _MAX_SERIES_LENGTH:
        raise Backtest360Error(
            f"Signal series has {len(signals):,} values, exceeding the engine's "
            f"per-series limit of {_MAX_SERIES_LENGTH:,}. Trim the date range "
            "or use a coarser bar frequency.",
            status=0,
            code="CLIENT_INVALID_SIGNALS",
        )
    # Coerce numpy scalar types (e.g. np.int64 from object-dtype Series) to
    # plain Python scalars so isinstance checks below work across numpy versions.
    values = [v.item() if hasattr(v, "item") else v for v in signals.tolist()]
    if any(
        v is None or pd.isna(v) or (isinstance(v, float) and not math.isfinite(v))
        for v in values
    ):
        raise Backtest360Error(
            "Signal series contains NaN/inf values. Signals must be integers "
            "in {-1, 0, 1}; drop or fill non-finite values (e.g. leading NaNs "
            "from .shift()/.rolling()) before calling.",
            status=0,
            code="CLIENT_INVALID_SIGNALS",
        )
    bad_types = [v for v in values if not isinstance(v, (bool, int, float))]
    if bad_types:
        examples = ", ".join(repr(v) for v in bad_types[:5])
        raise Backtest360Error(
            f"Signal values must be integers in {{-1, 0, 1}}. "
            f"Found non-numeric value(s): {examples}.",
            status=0,
            code="CLIENT_INVALID_SIGNALS",
        )
    bad = [v for v in values if not isinstance(v, bool) and isinstance(v, (int, float))
           and (float(v) != int(v) or int(v) not in (-1, 0, 1))]
    if bad:
        examples = ", ".join(str(v) for v in bad[:5])
        raise Backtest360Error(
            f"Signal values must be integers in {{-1, 0, 1}}. "
            f"Found out-of-range or fractional value(s): {examples}.",
            status=0,
            code="CLIENT_INVALID_SIGNALS",
        )
    d: dict[str, Any] = {
        "dates":  [ts.isoformat() for ts in signals.index],
        "values": [int(v) for v in values],
    }
    if name is not None:
        d["strategy_name"] = name
    return d


def _check_strategy_bounds(strategy: Strategy) -> None:
    """Reject strategies that exceed the engine's request bounds before any wire call."""
    if len(strategy.indicators) > _MAX_INDICATORS:
        raise Backtest360Error(
            f"Strategy declares {len(strategy.indicators)} indicators, exceeding "
            f"the engine's limit of {_MAX_INDICATORS} per request.",
            status=0,
            code="CLIENT_INVALID_STRATEGY",
        )
    for field in ("long_entry", "long_exit", "short_entry", "short_exit"):
        expr = getattr(strategy, field)
        if expr is not None and len(expr) > _MAX_EXPR_LENGTH:
            raise Backtest360Error(
                f"Condition expression '{field}' is {len(expr)} characters long, "
                f"exceeding the engine's limit of {_MAX_EXPR_LENGTH}. Split the "
                "logic into transform indicators or simplify the expression.",
                status=0,
                code="CLIENT_INVALID_STRATEGY",
            )


def _build_execution_wire(
    execution: Execution | None,
    costs: Costs | None,
    risk: Risk | None,
    sizing: Sizing | None,
    market_hours: MarketHours | None,
    settings: Settings | None,
) -> dict[str, Any]:
    """Merge all grouped-knob objects into the engine's flat execution dict."""
    d: dict[str, Any] = {}
    if execution is not None:
        d.update(execution.to_wire())
    if costs is not None:
        d.update(costs.to_wire())
    if risk is not None:
        d.update(risk.to_wire())
    if sizing is not None:
        d.update(sizing.to_wire())
    if market_hours is not None:
        d.update(market_hours.to_wire())
    if settings is not None:
        d.update(settings.to_wire())
    return d


def _build_backtest_body(
    strategy: Strategy,
    ohlcv: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    execution: Execution | None,
    costs: Costs | None,
    risk: Risk | None,
    sizing: Sizing | None,
    market_hours: MarketHours | None,
    settings: Settings | None,
    stats_keys: str | None = "ids",
) -> dict[str, Any]:
    _check_strategy_bounds(strategy)
    body: dict[str, Any] = {
        "data_source": {"ohlcv": _ohlcv_to_wire(ohlcv)},
        "strategy":    strategy.to_wire(),
    }
    # /api/latest-signal has no stats_keys field (it returns a signal, not
    # stats); callers that target it pass stats_keys=None to omit the key.
    if stats_keys is not None:
        body["stats_keys"] = stats_keys
    exec_wire = _build_execution_wire(execution, costs, risk, sizing, market_hours, settings)
    if exec_wire:
        body["execution"] = exec_wire
    if benchmark is not None:
        body["benchmark"] = {"ohlcv": _ohlcv_to_wire(benchmark)}
    return body


# Leg ids used by the single-strategy convenience methods when wrapping a call
# into the engine's leg-based `/api/backtest` contract.
_STRATEGY_LEG_ID = "strategy"
_BENCHMARK_LEG_ID = "benchmark"


def _build_strategy_leg(
    *,
    leg_id: str,
    ohlcv: pd.DataFrame,
    strategy: Strategy | None = None,
    signals_wire: dict[str, Any] | None = None,
    exec_wire: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one non-benchmark leg carrying either a strategy or a signal series."""
    leg: dict[str, Any] = {
        "id":          leg_id,
        "data_source": {"ohlcv": _ohlcv_to_wire(ohlcv)},
    }
    if strategy is not None:
        leg["strategy"] = strategy.to_wire()
    if signals_wire is not None:
        leg["signals"] = signals_wire
    if exec_wire:
        leg["execution"] = exec_wire
    return leg


def _build_benchmark_leg(*, leg_id: str, ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Build a benchmark leg — data only; the engine applies its always-long,
    zero-cost, no-stops benchmark preset."""
    return {
        "id":          leg_id,
        "data_source": {"ohlcv": _ohlcv_to_wire(ohlcv)},
        "benchmark":   True,
    }


def _build_backtest_envelope(
    *,
    legs: list[dict[str, Any]],
    stats_keys: str,
    reference: str | None = None,
) -> dict[str, Any]:
    """Wrap legs into the ``{run, legs}`` backtest request envelope."""
    run: dict[str, Any] = {"stats_keys": stats_keys}
    if reference is not None:
        run["reference"] = reference
    return {"run": run, "legs": legs}


def _result_from_legs(resp: dict[str, Any]) -> "Result":
    """Extract the strategy leg's result from a leg-based backtest response.

    The engine returns ``{"status", "run": {...}, "legs": [{"id", "result", ...}]}``.
    These convenience methods submit one strategy leg (id ``"strategy"``) plus an
    optional benchmark leg (id ``"benchmark"``); this pulls the strategy leg's
    result and, when a benchmark leg is present, folds its equity curve into the
    result's ``series`` so :attr:`Result.benchmark_equity` keeps working.
    """
    legs = resp.get("legs")
    if not isinstance(legs, list) or not legs:
        raise Backtest360Error(
            "Engine response contained no backtest legs.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )
    strategy_leg: dict[str, Any] | None = None
    benchmark_leg: dict[str, Any] | None = None
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if leg.get("id") == _BENCHMARK_LEG_ID or leg.get("benchmark"):
            benchmark_leg = leg
        elif strategy_leg is None:
            strategy_leg = leg
    if strategy_leg is None:
        strategy_leg = legs[0] if isinstance(legs[0], dict) else {}
    result_data = strategy_leg.get("result")
    if not isinstance(result_data, dict):
        raise Backtest360Error(
            "Engine response leg was missing its result payload.",
            status=0,
            code="CLIENT_MALFORMED_RESPONSE",
        )
    if benchmark_leg is not None:
        bench_result = benchmark_leg.get("result")
        if isinstance(bench_result, dict):
            bench_series = bench_result.get("series")
            if isinstance(bench_series, dict) and "strategy_equity" in bench_series:
                series = result_data.setdefault("series", {})
                if isinstance(series, dict):
                    series.setdefault("benchmark_equity", bench_series["strategy_equity"])
    relative = strategy_leg.get("relative")
    return Result(result_data, relative=relative if isinstance(relative, dict) else None)
