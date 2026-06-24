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
    convert_pattern_to_mask,
)

__all__ = [
    # Utility functions
    "CanIdPattern",
    # Core monitoring classes
    "CanMonitor",
    "CsvRecord",
    "DecodedMessage",
    "InvalidName",
    "InvalidPattern",
    "InvalidType",
    "JsonModel",
    # Console/display functionality
    "MessageTable",
    "ModelConfig",
    "MuxSelectorValue",
    # Error classes
    "UnknownMessage",
    "UnsupportedSystem",
    "collect_databases",
    "convert_pattern_to_mask",
    "find_sound_default",
    "get_platform_default_channel",
    "get_platform_default_driver",
]
