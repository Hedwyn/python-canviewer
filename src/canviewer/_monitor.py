"""
Manages the interactions with the CAN bus.
Monitors the bus and atttempts to decode the received messages when possible.
Queues the result to make them available to the frontend.

@date: 04.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations

import asyncio
import platform
from asyncio import Queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Iterator, Self, Union, cast

import cantools
from can import BusABC
from can import Message as CanMessage
from cantools.database.can import Database as CanDatabase  # type: ignore[attr-defined]
from cantools.database.can.message import Message as CanFrame
from cantools.database.namedsignalvalue import NamedSignalValue
from exhausterr import Err, Error, Ok, Result

from ._utils import CanIdPattern

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
class NamedDatabase:
    """
    Adds a label to cantools database.
    Standard use to to load using NamedDatabase.load_from_file(),
    which will keep the filename as identifier.
    """

    name: str
    database: CanDatabase
    path: Path | None = None

    @property
    def nodes(self) -> list[str]:
        """
        Labels given to the CAN nodes communicating on the bus
        using the given database.
        This will define the direction of messages based on the configured producer.
        """
        return [node.name for node in self.database.nodes]

    @property
    def messages(self) -> list[CanFrame]:
        """
        All the messages declared in the database.
        """
        return self.database.messages

    def get_message_by_name(self, name: str) -> CanFrame | None:
        """
        Returns
        -------
        CanFrame | None
            The frame registered under that name or None if it does not exist.
        """
        try:
            return self.database.get_message_by_name(name)
        except KeyError:
            return None

    @classmethod
    def load_from_file(cls, path: str | Path, name: str | None = None) -> Self:
        """
        Loads the database from the given `path`.
        """
        name = name or Path(path).stem
        loaded_db = cantools.database.load_file(path)
        assert isinstance(loaded_db, CanDatabase)
        return cls(
            name=name,
            database=loaded_db,
            path=Path(path),
        )


@dataclass
class DatabaseStore:
    """
    Stores multiple CAN databases at once and provides primitives
    to find messages in them.
    """

    databases: list[NamedDatabase] = field(default_factory=list)

    def __iter__(self) -> Iterator[NamedDatabase]:
        """
        Yields
        ------
        NamedDatabase
            All the stored databases.
        """
        yield from self.databases

    def find_message_and_db(
        self, message_name: str, db_name: str | None = None
    ) -> tuple[CanFrame, NamedDatabase]:
        """
        Looks for message `message_name` in all registered databases
        and returns both the message and the database in which it's declared.
        """
        for db in self.databases:
            if db_name is not None and db_name != db.name:
                continue
            if (msg := db.get_message_by_name(message_name)) is not None:
                return msg, db
        raise ValueError(
            f"Message named {message_name} was queried internally "
            "but not found in any DB"
        )

    def find_message(self, message_name: str, db_name: str | None = None) -> CanFrame:
        """
        Looks for message `message_name` in all registered databases.
        """
        return self.find_message_and_db(message_name, db_name=db_name)[0]

    @classmethod
    def from_files(cls, *db_files: str) -> Self:
        return cls([NamedDatabase.load_from_file(f) for f in db_files])

    def iter_periodic_messages(self) -> Iterator[CanFrame]:
        for db in self.databases:
            for message in db.messages:
                if message.cycle_time is not None:
                    yield message


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
    db_name: str | None = None

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
        store: DatabaseStore,
        loop: asyncio.AbstractEventLoop | None = None,
        mask: int = 0xFFFF_FFFF,
        id_pattern: CanIdPattern | int | None = None,
    ) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._queue: Queue[Result[DecodedMessage, UnknownMessage]] = Queue()
        self.store = store
        self._bus = bus
        # Starting the monitor
        self._loop.add_reader(self._bus, self.handler)
        self._mask = mask
        if id_pattern is not None:
            self._id_pattern: CanIdPattern | None = (
                id_pattern
                if isinstance(id_pattern, CanIdPattern)
                else CanIdPattern(id_pattern, ~mask)
            )
        else:
            self._id_pattern = None

    @property
    def bus(self) -> BusABC:  # TODO:type hint
        return self._bus

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
        # applying mask
        candidate_id = can_id & self._mask
        for db in self.store:
            try:
                frame = db.database.get_message_by_frame_id(candidate_id)
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
                    db_name=db.name,
                )

                return Ok(decoded_msg)
            except KeyError:
                continue

        return Err(UnknownMessage(candidate_id, msg))

    def handler(self) -> None:
        """
        Main callback on message reception.
        Attempts decoding the received data and queues the result.
        """
        next_message = self._bus.recv(timeout=0.0)

        if next_message is None:
            return

        can_id = next_message.arbitration_id
        if self._id_pattern is not None:
            if not (self._id_pattern.match(can_id)):
                return
        self._queue.put_nowait(self.decode_message(next_message))
