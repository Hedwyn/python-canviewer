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
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import Thread
from typing import Any, Callable, Generator, cast

import inotify.adapters
from can.bus import BusABC
from can.message import Message
from cantools.database import Message as CanFrame
from cantools.database.can import Database as CanDatabase
from cantools.database.can.signal import Signal as CanSignal
from cantools.database.namedsignalvalue import NamedSignalValue

type CanBasicTypes = float | int | str

_logger = logging.getLogger(__name__)


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
    if signal.offset:
        return float(signal.offset)

    if signal.scale is None or signal.scale == 1.0:
        return 0
    else:
        return 0.0
    raise ValueError(f"Could not find identity type of signal {signal}")


@dataclass
class ModelConfig:
    """
    Parameters
    ----------
    accumulate: bool
        If enabled, keeps stacking message values as a JSON list
        instead of overwriting a single dict value (the most recent one)
        Defaults to disabled.

    target_folder: str | None
        If passed, the temp folder for JSON files will be created in this location.
        If not, it will be created somewhere in /tmp

    preserve_files: bool
        Whether the temp folder and its JSON files should be deleted on exit.
        Disabled by default.
    """

    # placeholder for future user parametrization
    accumulate: bool = False
    target_folder: str | None = None
    preserve_files: bool = False
    enable_timestamping: bool = False


class JsonModel:
    """
    Modelizes a CAN database with one JSON file per message.
    """

    def __init__(
        self, database: CanDatabase, config: ModelConfig | None = None
    ) -> None:
        """
        Parameters
        ----------
        database: CanDatabase
            The CAN database for which we should generate JSON files

        config: ModelConfig
            User parameters on how the model should behave.
        """
        self._config = config or ModelConfig()
        self._tmp_folder: str | None = None
        self._database = database
        self._inotify_ignore_set: set[str] = set()

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
        with tempfile.TemporaryDirectory(
            dir=self._config.target_folder, delete=not self._config.preserve_files
        ) as tmp:
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
        self,
        message_name: str,
        message_values: dict[str, CanBasicTypes],
    ) -> None:
        """
        Updates the values in the JSON file of `message_name` with the given `values`.
        """
        self._inotify_ignore_set.add(message_name)
        message_json_path = self.get_message_json_path(message_name)
        # reading previous values
        with open(message_json_path) as f:
            previous_values = json.loads(f.read())

        if self._config.accumulate:
            if isinstance(previous_values, dict):
                # found the placeholder we created automatically
                # we can delete it
                previous_values = []
            previous_values.append(message_values)

        else:
            assert isinstance(previous_values, dict), (
                "Accumulate is disabled yet found a list in the JSON file"
                "User might have tampered the data manually"
            )
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
            json_data = json.loads(f.read())
            if isinstance(json_data, list):
                assert (
                    len(json_data) > 0
                ), "Found empty JSON data; user might have tampered file content manually"

                return json_data[-1]  # type: ignore
            return json_data  # type: ignore

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
            _logger.debug(
                "Ignoring %08x as it is not a known ID in our database",
                raw_message.arbitration_id,
            )
            return

        values = can_frame.decode(bytes(raw_message.data))
        if self._config.enable_timestamping:
            human_ts = datetime.fromtimestamp(raw_message.timestamp)
            values["LAST_RECEIVED"] = str(human_ts)  # type: ignore

        self.update_message_values(
            can_frame.name, cast(dict[str, CanBasicTypes], values)
        )

    def _run_inotify_watcher(
        self, bus: BusABC, on_error: Callable[[str, Exception], None] | None = None
    ) -> None:
        i = inotify.adapters.Inotify()

        i.add_watch(self.tmp_folder)

        for event in i.event_gen(yield_nones=False):
            assert event is not None, "`yield_nones` is True yet None was yielded"
            (_, type_names, path, filename) = event
            message_name = filename.removesuffix(".json")
            if not filename.endswith(".json"):
                continue
            if message_name in self._inotify_ignore_set:
                _logger.debug(
                    "Ignoring modifications on %s as this is an RX message",
                    message_name,
                )
                continue
            if "IN_MODIFY" in type_names:
                # triggering message send
                values = self.get_message_values(message_name)
                frame = self._database.get_message_by_name(message_name)
                _logger.info(
                    "%s modified, sending %s with values %s",
                    filename,
                    message_name,
                    values,
                )
                try:
                    bus.send(
                        Message(
                            arbitration_id=frame.frame_id, data=frame.encode(values)
                        )
                    )
                except Exception as exc:
                    _logger.debug(
                        "Error occured while encoding %s", message_name, exc_info=True
                    )
                    if on_error is None:
                        raise
                    on_error(message_name, exc)

    def start_inotify_watcher(
        self, bus: BusABC, on_error: Callable[[str, Exception], None] | None = None
    ) -> Thread:
        """
        Starts watching for changes in any of the monitored JSON files for messages.
        Messages that are received on the bus are automatically excluded from watching.
        Messages that are meant to be sent by this side (thus are not received on the bus)
        will be sent automatically on the given `bus` when a modification is detected.
        """
        watcher = Thread(
            target=self._run_inotify_watcher, args=(bus, on_error), daemon=True
        )
        watcher.start()
        return watcher
