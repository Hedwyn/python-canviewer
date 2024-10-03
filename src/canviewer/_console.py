"""
Tracks the message data and exports it as a renderable table.

@date: 03.10.2024
@author: Baptiste Pestourie
"""


from __future__ import annotations
# 3rd-party
from rich.table import Table
from exhausterr.results import Result, Ok, Err
# Local
from ._monitor import UnknownMessage, DecodedMessage

class MessageTable:
    """
    Stateful object; tracks the latest version of each message received
    and provides a primtive to export the current state as single renderable table.
    Messages are sorted by IDs.
    """

    def __init__(self, ignore_unknown_messages: bool = False) -> None:
        """
        Parameters
        ----------
        ignore_unknown_messages: bool
            If enabled, does not include unknown messages in the exported table.
        """
        self._ignore_unknown_messages = ignore_unknown_messages
        self._id_to_message: dict[int, Result[DecodedMessage, UnknownMessage]] = {}

    def update(self, message: Result[DecodedMessage, UnknownMessage]) -> None:
        """
        Updates the internal table based on the given result.
        """
        can_id = message.error.can_id if message.error else message.value.can_id
        # overriding the last version or creating a new one if first encounter
        self._id_to_message[can_id] = message

    def export(self) -> Table:
        """
        Returns
        -------
        Table
            The current tracked data as a renderable table.
        """
        table = Table(title="Messages")
        table.add_column("ID", justify="right", style="cyan", no_wrap=True)
        table.add_column("Name", style="magenta")
        table.add_column("Data", style="green")
        for can_id, result in self._id_to_message.items():
            match result:
                case Ok(decoded):
                    table.add_row(
                        f"{can_id:08x}",
                        str(decoded.message_name),
                        str(decoded.data),
                    )
                case Err(UnknownMessage(can_id, msg)):
                    if self._ignore_unknown_messages:
                        continue
                    table.add_row(
                        f"{can_id:08x}",
                        "[Unknown]",
                        " ".join((f"{b:02X}" for b in msg.data)),
                    )

        return table