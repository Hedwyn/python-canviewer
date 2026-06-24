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
from ._jsonify import JsonModel, ModelConfig, find_sound_default
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
    async_bus_poller,
    convert_pattern_to_mask,
)

__all__ = [
    "CanIdPattern",
    "CanMonitor",
    "CsvRecord",
    "DecodedMessage",
    "InvalidName",
    "InvalidPattern",
    "InvalidType",
    "JsonModel",
    "MessageTable",
    "ModelConfig",
    "MuxSelectorValue",
    "UnknownMessage",
    "UnsupportedSystem",
    "async_bus_poller",
    "collect_databases",
    "convert_pattern_to_mask",
    "find_sound_default",
    "get_platform_default_channel",
    "get_platform_default_driver",
]
