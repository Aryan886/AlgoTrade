"""
Custom exceptions for strict backtesting behavior.
"""


class StrictBacktestDataError(RuntimeError):
    """Raised when required point-in-time data is missing for strict execution."""
