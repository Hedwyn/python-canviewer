"""
Tracks the message data and exports it as a renderable table.

@date: 03.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
from typing import Final, Iterable, ClassVar, Any
from dataclasses import dataclass
from math import ceil

# 3rd-party
from rich.table import Table
from rich.pretty import Pretty
from rich.box import DOUBLE
from exhausterr import Result, Ok, Err, Error


# Local
from ._monitor import UnknownMessage, DecodedMessage, CanMessage


@dataclass
class InvalidName(Error):
    exception_cls: ClassVar[type[Exception]] = NameError
    name: str


@dataclass
class InvalidType(Error):
    exception_cls: ClassVar[type[Exception]] = ValueError
    value: Any


DEFAULT_WIDTH: Final[int] = 200
DEFAULT_HEIGHT: Final[int] = 40


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
        self._decoded_messages: dict[str, DecodedMessage] = {}
        self._raw_messages: dict[int, CanMessage] = {}
        self._id_filters = set((f for f in filters if isinstance(f, int)))
        self._name_filters = set((f for f in filters if isinstance(f, str)))
        self._plots: dict[str, dict[str, list[float]]] = {}
        self._page_height: int = DEFAULT_WIDTH
        self._page_width: int = DEFAULT_WIDTH

    def set_page_dimensions(self, width: int | None, height: int | None) -> int:
        """
        Returns
        -------
        int
            Number of pages required to render the entire data
        """
        self._page_height = height or self._page_height
        self._page_width = width or self._page_width
        return ceil(self.renderable_size() / self._page_height)

    def renderable_size(self) -> int:
        """
        Returns
        -------
        int
            Number of lines required to render the entire table
        """
        length = 0
        for decoded in self._decoded_messages.values():
            if self.filter_message_id(decoded.can_id) or self.filter_message_name(
                decoded.message_name
            ):
                continue
            length += len(decoded.data) + 2  # Need extra lines for brackets

        if not self._ignore_unknown_messages:
            length += len(self._raw_messages)
        return length

    def start_plot(self, message_signal_key: str) -> Result[None, InvalidName]:
        """
        Starts recording values for the message signal pair given.
        Parameters
        ----------
        message_signal_key: str
            Given as `message_name.signal_name`

        Returns
        -------
        Result[None, InvalidName]
            Nothing if OK,
            InvalidName if the message signal pair is invalid
        """
        try:
            message, signal = message_signal_key.split(".")
        except ValueError:
            return Err(InvalidName(message_signal_key))
        self._plots.setdefault(message, {})[signal] = []
        return Ok(None)

    def take_snapshot(self) -> dict[str, Any]:
        """
        Exports the current data as a CSV snapshot
        """
        snapshot = {}
        for msg_name, decoded in self._decoded_messages.items():
            for signal_name, value in decoded.data.items():
                snapshot[f"{msg_name}.{signal_name}"] = value

        return snapshot

    def export_plots_to_csv(self) -> list[str]:
        """
        Exports all plots to CSV files

        Returns
        -------
        str
            Path to the created CSV file
        """
        csv_paths = []
        for message, signals in self._plots.items():
            for signal, values in signals.items():
                csv_name = f"{message}.{signal}.csv"
                csv_paths.append(csv_name)
                with open(csv_name, "w") as f:
                    f.write(f"{message}.{signal}\n")
                    f.write("\n".join((str(v) for v in values)))
                    f.write("\n")
        return csv_paths

    def filter_message_id(self, can_id: int) -> bool:
        """
        Returns
        -------
        bool
            Whether the message should be filtered out
        """
        if not self._id_filters:
            return False

        return can_id not in self._id_filters

    def filter_message_name(self, name: str) -> bool:
        """
        Returns
        -------
        bool
            Whether the message should be filtered out
        """
        if not self._name_filters:
            return False

        return name not in self._name_filters

    def update(self, message: Result[DecodedMessage, UnknownMessage]) -> None:
        """
        Updates the internal table based on the given result.
        """
        # overriding the last version or creating a new one if first encounter
        match message:
            case Ok(decoded):
                self._decoded_messages[decoded.message_name] = decoded
                self._update_plots(decoded)

            case Err(UnknownMessage(can_id, raw_msg)):
                self._raw_messages[can_id] = raw_msg

    def _update_plots(self, message: DecodedMessage) -> Result[None, InvalidType]:
        """
        Updates all plots that are currently current for the passed
        decoded message.
        """
        message_plot_dict = self._plots.get(message.message_name)
        if message_plot_dict is None:
            return Ok(None)

        for signal, buffer in message_plot_dict.items():
            received = message.data[signal]
            try:
                new_value = float(message.data[signal])
            except ValueError:
                return Err(InvalidType(received))
            buffer.append(new_value)
        return Ok(None)

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
            min_width=self._page_width,
            width=self._page_width,
            expand=True,
            box=DOUBLE,
            header_style="bold cyan",
            title_style="bold underline green",
        )

    def export_single_message(self, message_id: int | str) -> Table | None:
        decoded: DecodedMessage | None = None
        if isinstance(message_id, int):
            for decoded_msg in self._decoded_messages.values():
                if decoded_msg.can_id == message_id:
                    decoded = decoded_msg
                    break
            else:
                return None
        else:
            decoded = self._decoded_messages.get(message_id)

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

    def export_paginated(self, page_index: int = 0) -> Table:
        """
        Returns
        -------
        Table
            The current tracked data as a renderable table.
        """
        page_starts = self._page_height * page_index
        page_ends = page_starts + self._page_height
        current_index = 0
        table = self._table_builder()
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Name", style="yellow")
        table.add_column("Binary", style="green")
        table.add_column("Decoded", style="blue")

        for decoded in self._decoded_messages.values():
            if current_index > page_ends:
                return table
            if any(
                (
                    self.filter_message_id(decoded.can_id),
                    self.filter_message_name(decoded.message_name),
                )
            ):
                continue
            current_index += len(decoded.data) + 2
            if current_index < page_starts:
                continue
            table.add_row(
                f"{decoded.can_id:08X}",
                str(decoded.message_name),
                self._format_binary_data(decoded.binary),
                Pretty(decoded.data),
            )

        if self._ignore_unknown_messages:
            return table
        for raw_msg in self._raw_messages.values():
            if current_index > page_ends:
                return table
            if self.filter_message_id(raw_msg.arbitration_id):
                continue

            current_index += 1
            if current_index < page_starts:
                continue
            table.add_row(
                f"{raw_msg.arbitration_id:08X}",
                "[Unknown]",
                self._format_binary_data(raw_msg.data),
                "",
            )
        return table
