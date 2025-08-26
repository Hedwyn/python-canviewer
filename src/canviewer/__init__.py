"""
Python CAN Viewer - A library for monitoring and decoding CAN bus messages

This package provides tools for monitoring CAN bus traffic, decoding messages
using CAN databases, and displaying the results in various formats.
"""

# Core monitoring functionality
# Console/table functionality for data display
from ._console import (
    CsvRecord,
    InvalidName,
    InvalidType,
    MessageTable,
)

# Entrypoint utilities that might be useful for library users
from ._entrypoints import (
    collect_databases,
)
from ._monitor import (
    CanMonitor,
    DecodedMessage,
    MuxSelectorValue,
    UnknownMessage,
    UnsupportedSystem,
    get_platform_default_channel,
    get_platform_default_driver,
)

# Utility functions and pattern matching
from ._utils import (
    CanIdPattern,
    InvalidPattern,
    convert_pattern_to_mask,
)

__all__ = [
    # Core monitoring classes
    "CanMonitor",
    "DecodedMessage",
    "MuxSelectorValue",
    # Error classes
    "UnknownMessage",
    "UnsupportedSystem",
    "InvalidName",
    "InvalidType",
    "InvalidPattern",
    # Console/display functionality
    "MessageTable",
    "CsvRecord",
    # Utility functions
    "CanIdPattern",
    "convert_pattern_to_mask",
    "get_platform_default_channel",
    "get_platform_default_driver",
    "collect_databases",
]
