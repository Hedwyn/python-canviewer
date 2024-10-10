"""
Tracks the message data and exports it as a renderable table.

@date: 03.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations
from typing import Iterable, ClassVar, Any
from dataclasses import dataclass

# 3rd-party
from rich.table import Table
from rich.pretty import Pretty
from rich.box import DOUBLE
from exhausterr.results import Result, Ok, Err
from exhausterr.errors import Error


# Local
from ._monitor import UnknownMessage, DecodedMessage


@dataclass
class InvalidName(Error):
    exception_cls: ClassVar[type[Exception]] = NameError
    name: str


@dataclass
class InvalidType(Error):
    exception_cls: ClassVar[type[Exception]] = ValueError
    value: Any


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
        self._plots: dict[str, dict[str, list[float]]] = {}

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

    def __del__(self) -> None:
        """
        Saves the plots as CSV on garbage collection
        """
        created_csv = self.export_plots_to_csv()
        print(f"CSV files created: {created_csv}")

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
                self._update_plots(decoded)

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
