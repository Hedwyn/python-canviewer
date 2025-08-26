"""
Creates a JSON model out of one or multiple CAN databases.
Spawns one JSON file per message, and provides primitives to read
and write their values.
This is meant to read and manipulate message values from a database in real time
directly from the filesystem.

@date: 26.08.2025
@author: Baptiste Pestourie
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, cast

from can.message import Message
from cantools.database import Message as CanFrame
from cantools.database.can import Database as CanDatabase
from cantools.database.can.signal import Signal as CanSignal
from cantools.database.namedsignalvalue import NamedSignalValue

type CanBasicTypes = float | int | str


def find_sound_default(signal: CanSignal) -> CanBasicTypes:
    """
    Finds a reasonable default value of a given declared CAN signal.
    """
    if signal.choices:
        choices = list(signal.choices.values())
        first_option = choices[0]
        if isinstance(first_option, NamedSignalValue):
            return str(first_option)
        return first_option
    if signal.minimum is not None:
        if signal.maximum:
            return float((signal.maximum + signal.minimum) / 2)
        return float(signal.minimum)
    if signal.offset is not None:
        return float(signal.offset)

    raise ValueError(f"Could not find identity type of signal {signal}")


@dataclass
class ModelConfig:
    # placeholder for future user parametrization
    pass


class JsonModel:
    def __init__(
        self, database: CanDatabase, config: ModelConfig | None = None
    ) -> None:
        self._config = config or ModelConfig()
        self._tmp_folder: str | None = None
        self._database = database

    @property
    def json_dump_options(self) -> dict[str, Any]:
        """
        Options that should be used when JSON dumping message values
        """
        return {
            "default": str,
            "indent": 4,
        }

    @property
    def tmp_folder(self) -> str:
        """
        Temp folder in which the JSON models are located
        """
        if self._tmp_folder is None:
            raise RuntimeError(
                "This model has not been opened yet. "
                "Create the temp files for the model by calling .open() method"
                " or using the context manager protocol"
            )
        return self._tmp_folder

    @contextmanager
    def open(self) -> Generator[str, None, None]:
        """
        Opens the model.
        Creates a temporary folder in the scope of this context manager containing
        the JSON files for all messages defined in the database to which this model
        is attached.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._tmp_folder = tmp
            self.create_json_files(tmp)
            yield tmp

    def build_message_default_json(self, message: CanFrame) -> dict[str, CanBasicTypes]:
        """
        Creates a default JSON representation of a message.
        All signal values will be defaulted.
        """
        return {signal.name: find_sound_default(signal) for signal in message.signals}

    def get_message_json_path(self, message_name: str) -> str:
        """
        Formats the JSON path that should be used for the message `message_name`
        """
        return os.path.join(self.tmp_folder, f"{message_name}.json")

    def create_json_files(self, target_folder: str) -> None:
        """
        Creates the JSON files for every message defined in the wrapped database.
        """
        for message in self._database.messages:
            fpath = self.get_message_json_path(message.name)
            with open(fpath, "w+") as f:
                f.write(
                    json.dumps(
                        self.build_message_default_json(message),
                        **self.json_dump_options,
                    ),
                )

    def update_message_values(
        self, message_name: str, message_values: dict[str, CanBasicTypes]
    ) -> None:
        """
        Updates the values in the JSON file of `message_name` with the given `values`.
        """
        previous_values = self.get_message_values(message_name)
        previous_values.update(message_values)
        with open(self.get_message_json_path(message_name), "w+") as f:
            f.write(json.dumps(previous_values, **self.json_dump_options))

    def get_message_values(self, message_name: str) -> dict[str, CanBasicTypes]:
        """
        Returns
        -------
        dict[str, CanBasicTypes]
            Current signal values for the message `message_name`
        """
        message_json_path = self.get_message_json_path(message_name)
        # reading previous values
        with open(message_json_path) as f:
            return json.loads(f.read())  # type: ignore

    def encode_message(self, message_name: str) -> bytes:
        """
        Returns
        -------
        dict[str, CanBasicTypes]
            Current signal values for the message `message_name`
        """
        current_values = self.get_message_values(message_name)
        encoder = self._database.get_message_by_name(message_name).encode
        return encoder(current_values)

    def update_model(self, raw_message: Message) -> None:
        """
        Given a message received on the CAN bus `raw_message`,
        checks if the message targets something from the wrapped database
        and decodes and updates the values accordingly
        """
        try:
            can_frame = self._database.get_message_by_frame_id(
                raw_message.arbitration_id
            )
        except KeyError:
            # Not for us - nothing to do
            return

        values = can_frame.decode(bytes(raw_message.data))

        self.update_message_values(
            can_frame.name, cast(dict[str, CanBasicTypes], values)
        )
