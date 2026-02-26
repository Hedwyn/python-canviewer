"""
Utilities to replay candumps.

@date: 08.01.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, overload

import can
from can.message import Message


@dataclass
class ReplayableMessage:
    """
    Data required to replay a given message.
    """

    can_id: int
    data: bytes
    relative_time: float
    channel: str | None = None

    def to_python_can_message(self) -> Message:
        """
        Converts this dataclass to a python-can Message
        that can be passed to a python-can bus.
        """
        return Message(
            arbitration_id=self.can_id,
            data=self.data,
        )


# --- Parsing Errors --- #
class DumpParseError(Exception):
    pass


class NumericConversionError(DumpParseError):
    pass


class IncorrectFieldsCount(DumpParseError):
    pass


@overload
def convert_to(numeric_type: type[int], number_str: str, base: int = 10) -> int: ...


@overload
def convert_to(numeric_type: type[float], number_str: str, base: int = 10) -> float: ...


def convert_to(
    numeric_type: type[int | float], number_str: str, base: int = 10
) -> int | float:
    """
    Tiny wrapper on top of numeric string conversions raising `NumericConversionError`
    on invalid values.
    """

    try:
        if numeric_type is int:
            return int(number_str, base=base)
        return float(number_str)
    except ValueError:
        raise NumericConversionError(number_str)


def split(string: str, expected_len: int, separator: str | None = None) -> list[str]:
    """
    Equivalent to string.split(separator) but raises `IncorrectFieldsCount`
    if the number of fields does not equal `expected_len`.
    """
    fields = string.split(separator)
    if len(fields) != expected_len:
        raise IncorrectFieldsCount(f"expected {expected_len}, got {len(fields)}")
    return fields


def parse_candump(
    dump: Iterable[str], is_stdout: bool = False
) -> Iterator[ReplayableMessage]:
    """
    Parses a candump.
    If `is_stdout`, the format is expected to the one candump uses when
    emitting to standard output, as opposed to the one used for file usage.
    Should be used when the candump was produced by piping stdout to a file
    instead of using the dedicated option directly.
    """
    start_time: float | None = None
    for line in dump:
        if not line:
            continue
        if is_stdout:
            _ts, channel, _id, _dlc, *_data = line.split()
            dlc = convert_to(int, _dlc[1:-1])
            data = convert_to(int, "".join(_data), base=16).to_bytes(length=dlc)
        else:
            _ts, channel, _id_and_data = split(line, 3)
            _id, _payload = _id_and_data.split("#")
            dlc = len(_payload) // 2
            data = convert_to(int, _payload, base=16).to_bytes(length=dlc)
        id = convert_to(int, _id, base=16)
        ts = convert_to(float, _ts[1:-1])
        if start_time is None:
            start_time = ts
        rel_time = ts - start_time
        yield ReplayableMessage(id, data, rel_time, channel=channel)


async def replay(
    messages: Iterable[ReplayableMessage],
    dest_channel: str,
    src_channel: str | None = None,
) -> None:
    """
    Replays the CAN `messages` on `dest_channel`.
    If `src_channel` is passed only replays the messages from that channel.
    """
    start_time: float | None = None
    with can.Bus(interface="socketcan", channel=dest_channel) as bus:
        for message in messages:
            if src_channel and message.channel != src_channel:
                continue
            now = time.time()
            if start_time is None:
                start_time = now
            delivery_time = start_time + message.relative_time
            time_delta = delivery_time - now
            await asyncio.sleep(time_delta)
            bus.send(message.to_python_can_message())
