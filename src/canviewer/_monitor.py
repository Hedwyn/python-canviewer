"""
Manages the interactions with the CAN bus.
Monitors the bus and atttempts to decode the received messages when possible.
Queues the result to make them available to the frontend.

@date: 04.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
import asyncio
from asyncio import Queue
from typing import Iterator, Union, ClassVar, cast
import platform
from dataclasses import dataclass
from exhausterr import Result, Ok, Err, Error
from can import Message as CanMessage, BusABC
from cantools.database.can import Database as CanDatabase  # type: ignore[attr-defined]
from cantools.database.can.message import Message as CanFrame
from cantools.database.namedsignalvalue import NamedSignalValue

# type hinting
CanTypes = Union[int, float, str, NamedSignalValue]
MessageDict = dict[str, CanTypes]


@dataclass
class UnknownMessage(Error):
    """
    Emitted when a message is not registered in any of the tracked databases
    """

    description: ClassVar[str | None] = (
        "CAN ID {can_id} is not registered in any of the tracked databases"
    )
    exception_cls: ClassVar[type[Exception]] = ValueError

    # error parameters
    can_id: int
    message: CanMessage

    def __hash__(self) -> int:
        """
        Uses the CAN Id as the main key
        """
        return self.can_id


@dataclass
class UnsupportedSystem(Error):
    """
    CAN operations are not supported on the target system
    """

    description: ClassVar[str | None] = "Unsupported system: {system}"
    exception_cls: ClassVar[type[Exception]] = NotImplementedError

    # error parameters
    system: str


@dataclass
class MuxSelectorValue:
    """
    A mux selector value
    """

    name: str
    value: CanTypes


@dataclass
class DecodedMessage:
    """
    Simple container for a decoded message,
    keeps track of the original CAN ID and message name
    as stated in the CAN database
    """

    can_id: int
    timestamp: float
    frame_name: str
    binary: bytearray
    data: MessageDict
    mux_selectors: tuple[MuxSelectorValue, ...] = ()

    def __hash__(self) -> int:
        """
        Uses the message as the main key
        """
        return hash(self.message_name)

    @property
    def message_name(self) -> str:
        """
        Returns
        -------
        str
            The name of the message, combining the frame name and the mux values.
        """
        formatted_selectors = (
            f"[{mux.name}={mux.value}]" for mux in self.mux_selectors
        )
        return self.frame_name + "".join(formatted_selectors)


def get_platform_default_channel() -> Result[str, UnsupportedSystem]:
    """
    Returns
    -------
    Result[str, UnsupportedSystem]
        The default channel to use for this system.
        UnsupportedSystem is the local platform is not supported
    """
    local_system = platform.system()
    match local_system:
        case "Windows":
            return Ok("PCAN_USBBUS1")
        case "Linux":
            return Ok("can0")
        case _:
            return Err(UnsupportedSystem(local_system))


def get_platform_default_driver() -> Result[str, UnsupportedSystem]:
    """
    Returns
    -------
    Result[str, UnsupportedSystem]
        The default driver to use for this system.
        UnsupportedSystem is the local platform is not supported
    """
    local_system = platform.system()
    match local_system:
        case "Windows":
            return Ok("pcan")
        case "Linux":
            return Ok("socketcan")
        case _:
            return Err(UnsupportedSystem(local_system))


class CanMonitor:
    """
    Monitors a bus and decodes received messages when possible
    """

    def __init__(
        self,
        bus: BusABC,
        *can_dbs: CanDatabase,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._queue: Queue[Result[DecodedMessage, UnknownMessage]] = Queue()
        self._dbs = list(can_dbs)
        self._bus = bus
        # Starting the monitor
        self._loop.add_reader(self._bus, self.handler)

    @property
    def queue(self) -> Queue:
        """
        Returns
        -------
        Queue
            All the decoding results for received messages.
        """
        return self._queue

    def get_mux_selector_values(
        self, frame: CanFrame, data: MessageDict
    ) -> Iterator[MuxSelectorValue]:
        """
        Returns
        -------
        int | None
            The index of the mux if the frame is a mux, None otherwise.
        """
        if frame.signal_tree is None:
            return
        for entry in frame.signal_tree:
            if not isinstance(entry, dict):
                # non-mux are just normal names, not dict
                continue
            for mux_name in entry:
                selected_value = data.get(mux_name)
                if selected_value is None:
                    continue

                yield MuxSelectorValue(mux_name, selected_value)

    def decode_message(self, msg: CanMessage) -> Result[DecodedMessage, UnknownMessage]:
        """
        Looks for a matching message in the list of tracked databases.
        Decodes the message if possible otherwise returns a `UnknownMessage`
        with the received message data
        """
        can_id = msg.arbitration_id
        for db in self._dbs:
            try:
                frame = db.get_message_by_frame_id(can_id)
                decoded_data = frame.decode(msg.data)  # type: ignore[assignment]
                # Have to cast because cantools does not provide necessary overloads
                # for decode -> when decode_containers is False, returned type is dict
                decoded_data = cast(MessageDict, decoded_data)
                # checking if frame is a mux

                selectors = tuple(self.get_mux_selector_values(frame, decoded_data))
                decoded_msg = DecodedMessage(
                    can_id=can_id,
                    timestamp=msg.timestamp,
                    frame_name=frame.name,
                    binary=msg.data,
                    data=cast(MessageDict, decoded_data),
                    mux_selectors=selectors,
                )

                return Ok(decoded_msg)
            except KeyError:
                continue

        return Err(UnknownMessage(can_id, msg))

    def handler(self) -> None:
        """
        Main callback on message reception.
        Attempts decoding the received data and queues the result.
        """
        next_message = self._bus.recv(timeout=0.0)
        if next_message is None:
            return
        self._queue.put_nowait(self.decode_message(next_message))
