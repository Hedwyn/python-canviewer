"""
Tracks the message data and exports it as a renderable table.

@date: 03.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
from typing import Iterable

# 3rd-party
from rich.table import Table
from rich.pretty import Pretty
from rich.box import DOUBLE
from exhausterr.results import Result, Ok, Err

# Local
from ._monitor import UnknownMessage, DecodedMessage


class MessageTable:
    """
    Stateful object; tracks the latest version of each message received
    and provides a primtive to export the current state as single renderable table.
    Messages are sorted by IDs.
    """

    def __init__(
        self,
        ignore_unknown_messages: bool = False,
        filters: Iterable[int | str] = (),
    ) -> None:
        """
        Parameters
        ----------
        ignore_unknown_messages: bool
            If enabled, does not include unknown messages in the exported table.
        """
        self._ignore_unknown_messages = ignore_unknown_messages
        self._id_to_message: dict[int, Result[DecodedMessage, UnknownMessage]] = {}
        self._name_to_message: dict[str, DecodedMessage] = {}
        self._id_filters = set((f for f in filters if isinstance(f, int)))
        self._name_filters = set((f for f in filters if isinstance(f, str)))

    def filter_message_id(self, can_id: int) -> bool:
        """
        Returns
        -------
        bool
            Whether the message should be filtered in or out
        """
        if not self._id_filters:
            return True

        return can_id in self._id_filters

    def filter_message_name(self, name: str) -> bool:
        """
        Returns
        -------
        bool
            Whether the message should be filtered in or out
        """
        if not self._name_filters:
            return True

        return name in self._name_filters

    def update(self, message: Result[DecodedMessage, UnknownMessage]) -> None:
        """
        Updates the internal table based on the given result.
        """
        can_id = message.error.can_id if message.error else message.value.can_id
        # overriding the last version or creating a new one if first encounter
        self._id_to_message[can_id] = message
        match message:
            case Ok(decoded):
                self._name_to_message[decoded.message_name] = decoded

    def _format_binary_data(self, data: bytes | bytearray) -> str:
        """
        A simple string formatter for binary data
        """
        return " ".join((f"{b:02X}" for b in data))

    def _table_builder(self) -> Table:
        """
        Default builder for rich's Table
        """
        return Table(
            title="Messages",
            width=180,
            expand=True,
            box=DOUBLE,
            header_style="bold cyan",
            title_style="bold underline green",
        )

    def export_single_message(self, message_id: int | str) -> Table | None:
        if isinstance(message_id, int):
            last_received = self._id_to_message.get(message_id)
            if last_received is None:
                return None
            match last_received:
                case Ok(d):
                    decoded = d
                case Err(_):
                    return None
        else:
            decoded = self._name_to_message.get(message_id)
            if decoded is None:
                return None

        table = self._table_builder()
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Name", style="yellow")
        table.add_column("Binary", style="green")
        table.add_column("Decoded", style="blue")
        for signal in decoded.data:
            table.add_column(signal, style="rgb(128,128,128)")

        table.add_row(
            f"{decoded.can_id:08X}",
            str(decoded.message_name),
            self._format_binary_data(decoded.binary),
            *(Pretty(val) for val in decoded.data.values()),
        )

        return table

    def export(self) -> Table:
        """
        Returns
        -------
        Table
            The current tracked data as a renderable table.
        """
        table = self._table_builder()
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Name", style="yellow")
        table.add_column("Binary", style="green")
        table.add_column("Decoded", style="blue")

        for can_id, result in self._id_to_message.items():
            if not self.filter_message_id(can_id):
                continue
            match result:
                case Ok(decoded):
                    if not self.filter_message_name(decoded.message_name):
                        continue
                    table.add_row(
                        f"{can_id:08X}",
                        str(decoded.message_name),
                        self._format_binary_data(decoded.binary),
                        Pretty(decoded.data),
                    )
                case Err(UnknownMessage(can_id, msg)):
                    if self._ignore_unknown_messages:
                        continue
                    table.add_row(
                        f"{can_id:08X}",
                        "[Unknown]",
                        self._format_binary_data(msg.data),
                        "",
                    )

        return table
