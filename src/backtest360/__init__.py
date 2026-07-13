"""Backtest360 Python client."""

from importlib.metadata import PackageNotFoundError, version

from backtest360.client import Backtest360Error, Client, Result
from backtest360.strategy import Costs, Execution, MarketHours, Risk, Settings, Sizing, Strategy

try:
    __version__ = version("backtest360")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"

__all__ = [
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
