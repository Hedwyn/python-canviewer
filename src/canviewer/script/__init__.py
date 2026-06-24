"""
Scripting utilities for canviewer.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from canviewer.script._codegen import (
    DEFAULT_NODE_NAME,
    NEW_LINES_AFTER_CLS,
    BuiltinNameConversions,
    CodegenOptions,
    NameConversionFn,
    _find_signal_type,
    _generate_main,
    _generate_message_code,
    _generate_node,
    _generate_signal_fields,
    build_module,
    camel_to_snake_case,
    format_code,
    generate_dataclasses,
    make_canonical,
    sanity_checks,
    transpile_database,
)
from canviewer.script._core import (
    AlmostEqual,
    Condition,
    DifferentThan,
    Equal,
    SignalContainer,
    SignalValue,
    Tolerance,
    Waiter,
    get_annotation,
    get_annotations,
    get_signal_map,
    iter_annotations,
    monitor,
    run_dispatcher,
)

__all__ = [
    "DEFAULT_NODE_NAME",
    "NEW_LINES_AFTER_CLS",
    "AlmostEqual",
    "BuiltinNameConversions",
    "CodegenOptions",
    "Condition",
    "DifferentThan",
    "Equal",
    "NameConversionFn",
    "SignalContainer",
    "SignalValue",
    "Tolerance",
    "Waiter",
    "_find_signal_type",
    "_generate_main",
    "_generate_message_code",
    "_generate_node",
    "_generate_signal_fields",
    "build_module",
    "camel_to_snake_case",
    "format_code",
    "generate_dataclasses",
    "get_annotation",
    "get_annotations",
    "get_signal_map",
    "iter_annotations",
    "make_canonical",
    "monitor",
    "run_dispatcher",
    "sanity_checks",
    "transpile_database",
]
