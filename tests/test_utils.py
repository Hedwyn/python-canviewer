"""
Test suite for shared utilities

@date: 05.11.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
import pytest
from canviewer import CanIdPattern, convert_pattern_to_mask
from canviewer._utils import InvalidPattern


@pytest.mark.parametrize(
    "pattern, value, expected",
    [
        [(0x0120_0000, 0xFFF0_0000), 0x0123_4567, True],
        [(0x1120_0000, 0xFFF0_0000), 0x01234567, False],
        [(0x0023_4000, 0x00FF_F000), 0x01234567, True],
        [(0x0023_0000, 0x00FF_F000), 0x01234567, False],
    ],
)
def test_pattern_matching(pattern: tuple[int, int], value: int, expected: bool) -> None:
    """
    Verifies the `match` method of `CanIdPattern`.
    """
    id_pattern = CanIdPattern(*pattern)
    assert id_pattern.match(value) == expected


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("1234", 0x1234),
        ("*1234", CanIdPattern(0x1234, 0x0000_FFFF)),
        ("1234*", CanIdPattern(0x1234_0000, 0xFFFF_0000)),
        ("12345*", CanIdPattern(0x1234_5000, 0xFFFF_F000)),
        ("12*", CanIdPattern(0x1200_0000, 0xFF00_0000)),
        ("1234$", InvalidPattern),
        ("12$4*", InvalidPattern),
        ("*12$4", InvalidPattern),
    ],
)
def test_convert_pattern_to_mask(
    pattern: str, expected: CanIdPattern | int | type[Exception]
) -> None:
    """
    Verifies the `convert_pattern_to_mask` function.
    """
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            convert_pattern_to_mask(pattern)
    else:
        obtained = convert_pattern_to_mask(pattern)
        assert obtained == expected, obtained
