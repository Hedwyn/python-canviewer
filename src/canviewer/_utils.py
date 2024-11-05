"""
Miscaellenous utilities

@date: 05.11.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
from dataclasses import dataclass


class InvalidPattern(Exception):
    """
    Whenever the pattern passed by the user is invalid
    """

    pass


@dataclass(eq=True)
class CanIdPattern:
    """
    A match pattern for a CAN message.

    Bits captured by the `mask` are compared to the ones
    in `values`.
    Can ID latches the pattern if (can_id & mask) == value
    """

    value: int
    mask: int = 0xFFFF_FFFF

    def match(self, can_id: int) -> bool:
        """
        Returns
        -------
        bool
            True if the CAN ID should be filtered in,
            False for filtering out
        """
        return (can_id & self.mask) == self.value

    def __str__(self) -> str:
        """
        Returns
        -------
        str
            Friendly representation that uses hex as default for integers
        """
        return f"0x{self.value:08x}; 0x{self.mask:08x}"

    def __repr__(self) -> str:
        """
        Returns
        -------
        str
            A human readable representation of the pattern
        """
        return f"Pattern: 0x{self.value:08x}; Mask: {self.mask:08x}"


def _convert_from_hex(pattern: str) -> int:
    """
    A small helper that raises the proper user exception when getting invalid
    pattern values
    """
    try:
        return int(pattern, 16)
    except ValueError as exc:
        raise InvalidPattern(
            f"Got invalid characters after `*` in pattern, expected hex digits: {pattern}"
        ) from exc


def convert_pattern_to_mask(pattern: str) -> CanIdPattern | int:
    """
    Converts a match pattern given by the user
    to a acceptance mask.

    Examples
    --------
    *123 should expand to 0x00000FFF, 0x00000123
    123* should expand to 0x00000FFF,0x12300000
    0000FFFF, 1234 should expand
    """
    if "," in pattern:
        try:
            mask, pattern = pattern.split(",")
        except ValueError as exc:
            raise InvalidPattern(
                f"Passing pattern with `,` expects only two values, got {pattern}"
            ) from exc
        return CanIdPattern(int(mask, 16), int(pattern, 16))

    if pattern.startswith("*"):
        pattern = pattern[1:]
        pattern_value = _convert_from_hex(pattern)
        # We pad based on 32 bits masks, hence 8 hex digits
        value_shift = len(pattern) * 4  # each digit is 4 bits
        mask = (1 << value_shift) - 1
        return CanIdPattern(pattern_value, mask)

    if pattern.endswith("*"):
        pattern = pattern[:-1]
        pattern_value = _convert_from_hex(pattern)
        # We pad based on 32 bits masks, hence 8 hex digits
        bitsize = len(pattern) * 4  # each digit is 4 bits
        value_shift = 32 - bitsize
        pattern_value <<= value_shift
        mask = (2**bitsize - 1) << value_shift
        return CanIdPattern(pattern_value, mask)

    return _convert_from_hex(pattern)
