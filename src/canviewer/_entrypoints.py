"""
Application entrypoint(s) for this package.
CLI tools are based on click and will be installed
automatically as package scripts.

@date: 04.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations

# built-in
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import IO, Callable, Iterable, Iterator, Literal

# 3rd-party
import asciichartpy as acp
import can
import cantools
import click
import pyperclip
import rich
from cantools.database.can import Database
from exhausterr import Err, Ok
from rich.console import Console, Group
from rich.live import Live

from ._console import MessageTable
from ._jsonify import JsonModel, ModelConfig

# Local
from ._monitor import (
    CanMonitor,
    get_platform_default_channel,
    get_platform_default_driver,
)
from ._utils import CanIdPattern, InvalidPattern, convert_pattern_to_mask
from ._player import parse_candump, replay, DumpParseError

_logger = logging.getLogger(__name__)

# Number of lines for the actual table
HEIGHT_MARGIN: int = 10
WIDTH_MARGIN: int = 10
DEFAULT_HEIGHT = 30
ZOOM_FACTOR = 1.1
PLOT_MAX_SIZE = 50


class UserCommands(Enum):
    """
    All the commands which can be received from the user
    """

    TAKE_SNAPSHOT = auto()


@dataclass
class UserInterface:
    """
    Manages the interaction with the user
    """

    page_index: int = 0
    total_pages: int = 1
    dispatcher: dict[UserCommands, Callable[[], None]] = field(default_factory=dict)
    log: str = ""
    height: int = DEFAULT_HEIGHT

    def on_input(self, stream: IO[str]) -> None:
        """
        Process user input from the given stream
        """
        # clearing previous log
        self.log = ""
        command = stream.readline().strip()
        match command:
            case "":
                self.page_index = (self.page_index + 1) % self.total_pages

            case "b":
                self.page_index = (self.page_index - 1) % self.total_pages

            case "s":
                if (cmd := self.dispatcher.get(UserCommands.TAKE_SNAPSHOT)) is not None:
                    match cmd():
                        case Ok(_):
                            self.log = "[green]Took snapshot successfully !"
                        case Err(err):
                            self.log = str(err)

            # zoom in / zoom out commands
            case "+" | "++" | "+++":
                zoom_factor = ZOOM_FACTOR ** len(command)
                self.height = round(self.height / zoom_factor)

            case "-" | "--" | "---":
                zoom_factor = ZOOM_FACTOR ** len(command)
                self.height = round(self.height * zoom_factor)

            case _:
                if command.isnumeric():
                    try:
                        idx = int(command) - 1
                    except ValueError:
                        # ignoring
                        return

                    if 0 <= idx < self.total_pages:
                        self.page_index = idx
                else:
                    self.log = f"[red]Unknown command: {command}"

    def page_indication(self) -> str:
        return (
            f"Page {self.page_index + 1}/{self.total_pages}"
            " (Press enter go to next page)"
        )


async def _canviewer(
    channel: str,
    driver: str,
    databases: Iterable[Database],
    ignore_unknown_messages: bool,
    message_filters: Iterable[int | str],
    single_message: str | None = None,
    record_signals: list[str] = [],
    plot_signals: list[str] = [],
    inline: bool = False,
    snapshot_type: Literal["csv", "json"] = "csv",
    mask: int = 0xFFFF_FFFF,
    id_pattern: CanIdPattern | int | None = None,
    always_show_value: bool = False,
) -> None:
    """
    Main asynchronous runner for the console application.

    Parameters
    ----------
    channel: str
        The name of the CAN channel to monitor

    driver: str
        The name of the CAN driver (interface)

    databases: Iterable[Database]
        The paths to .kcd files or to a folder containing kcd files
    """
    message_table = MessageTable(
        ignore_unknown_messages=ignore_unknown_messages, filters=message_filters
    )
    console = Console()
    loop = asyncio.get_event_loop()
    started = datetime.now()

    def on_snapshot() -> Ok[None]:
        dict_data = message_table.take_snapshot()
        fname = (
            "snapshot_canviewer_"
            + started.strftime("%Y_%m_%d_%H_%M_%S")
            + "."
            + snapshot_type
        )
        now = time.time()
        if not os.path.exists(fname) and snapshot_type == "csv":
            with open(fname, "w+") as f:
                header = ",".join(("timestamp", *dict_data.keys()))
                f.write(header + "\n")
        # converting to CSV
        with open(fname, "a+") as f:
            match snapshot_type:
                case "json":
                    dict_data["timestamp"] = now
                    f.write(json.dumps(dict_data, default=str, indent=4))

                case "csv":
                    values = ",".join((str(now), *[str(v) for v in dict_data.values()]))
                    f.write(values + "\n")
        return Ok()

    for message_signal in (*record_signals, *plot_signals):
        if not message_table.start_plot(message_signal):
            click.echo(f"Invalid message signal: {message_signal}")
            return

    with can.Bus(interface=driver, channel=channel) as bus:
        with Live(console=console, screen=not inline) as live:
            interface = UserInterface(height=console.size.height)

            # registering commands
            interface.dispatcher[UserCommands.TAKE_SNAPSHOT] = on_snapshot
            loop.add_reader(sys.stdin, interface.on_input, sys.stdin)
            backend = CanMonitor(
                bus,
                *databases,
                mask=mask,
                id_pattern=id_pattern,
                always_show_value=always_show_value,
            )
            try:
                while True:  # Ctrl + C to leave
                    message = await backend.queue.get()
                    message_table.update(message)

                    page_height = interface.height - HEIGHT_MARGIN
                    page_width = console.size.width - WIDTH_MARGIN
                    interface.total_pages = message_table.set_page_dimensions(
                        page_width, page_height
                    )
                    if single_message is not None:
                        renderable_table = message_table.export_single_message(
                            single_message
                        )
                        if renderable_table is None:
                            continue
                    else:
                        renderable_table = message_table.export_paginated(
                            interface.page_index
                        )

                    plots = []
                    for message_signal in plot_signals:
                        match message_table.get_plot_by_name(message_signal):
                            case Ok(data):
                                _, y = data
                                plots.append(
                                    Group(
                                        message_signal,
                                        acp.plot(y[:PLOT_MAX_SIZE]),
                                    )
                                )

                            case Err(error):
                                interface.log = f"[red]{error}"

                    if interface.total_pages > 1 and single_message is None:
                        renderable = Group(
                            interface.page_indication(),
                            interface.log,
                            *plots,
                            renderable_table,
                        )
                    else:
                        renderable = Group(*plots, renderable_table)

                    live.update(renderable)

            finally:
                csv_paths = message_table.export_plots_to_csv()
                if csv_paths:
                    click.echo(f"CSV files created: {csv_paths}")


def collect_databases(*paths: str) -> Iterator[str]:
    """
    Based on a selection or folder and/or direct paths to
    CAN databases, discovers the databases within the folders
    and yields a flattened list of all discovered databases.

    Yields
    ------
    str
        Path to a KCD or DBC database to loadd
    """
    for path in paths:
        if os.path.isdir(path):
            yield from (
                os.path.join(path, f)
                for f in os.listdir(path)
                if f.endswith(".kcd") or f.endswith(".dbc")
            )
        else:
            yield path


@click.command()
@click.option(
    "-c", "--channel", default=None, type=str, help="Name of the CAN channel to monitor"
)
@click.option(
    "-d",
    "--driver",
    default=None,
    type=str,
    help="Specifies which CAN driver to use if multiple available",
)
@click.option(
    "-db",
    "--databases",
    default=(),
    type=str,
    multiple=True,
    help="Path to .kcd files or to a folder containing kcd files",
)
@click.option(
    "-f",
    "--filters",
    default=(),
    type=str,
    multiple=True,
    help="Either a name or a numeric ID, only passed messages will be displayed",
)
@click.option(
    "-s",
    "--single-message",
    type=str,
    default=None,
    help="Tracks a single message, shows a custom table with one column per signal",
)
@click.option(
    "-i",
    "--ignore-unknown-messages",
    is_flag=True,
    help="Hides messages that are not declared in one of your databases",
)
@click.option(
    "-r",
    "--record-signals",
    multiple=True,
    type=str,
    help=(
        "Records the values for a given signal, exports them to CSV on exiting.\n"
        "If you'd like to also plot the signal in realtime, use -pl/--plot instead\n"
        "You shall pass your target signal as message_name.signal_name"
    ),
)
@click.option(
    "-pl",
    "--plot-signals",
    multiple=True,
    type=str,
    help=(
        "Plots the values for a given signal, exports them to CSV on exiting.\n"
        "You shall pass your target signal as message_name.signal_name"
    ),
)
@click.option(
    "-n",
    "--inline",
    is_flag=True,
    help="Disables full-screen",
)
@click.option(
    "-sf",
    "--snapshot-format",
    type=click.Choice(["json", "csv"]),
    default="csv",
    help="Format to use for snapshots",
)
@click.option(
    "-mk",
    "--mask",
    type=str,
    default="FFFFFFFF",
    help="Applies this mask on CAN IDs before feeding to decoder",
)
@click.option(
    "-p",
    "--pattern",
    type=str,
    default=None,
    help="Filter in all the messages following that pattern",
)
@click.option(
    "-asv",
    "--always-show-value",
    is_flag=True,
    help="Shows the value instead of the name for signals using named values",
)
def canviewer(
    channel: str | None,
    driver: str | None,
    databases: Iterable[str],
    filters: Iterable[str],
    single_message: str | None,
    ignore_unknown_messages: bool,
    record_signals: list[str],
    plot_signals: list[str],
    inline: bool,
    snapshot_format: Literal["json", "csv"],
    mask: str,
    pattern: str | None,
    always_show_value: bool,
) -> None:
    """
    For every CAN ID found on the CAN bus,
    displays the data for the last message received.
    If the message is declared in one of the passed databases,
    shows the decoded data.
    """

    try:
        id_pattern = convert_pattern_to_mask(pattern) if pattern else None
    except InvalidPattern as exc:
        raise click.BadParameter(str(exc), param_hint="pattern") from exc

    if channel is None:
        match get_platform_default_channel():
            case Ok(channel_name):
                channel = channel_name
            case Err(error):
                click.echo(str(error))
                return

    if driver is None:
        match get_platform_default_driver():
            case Ok(driver_name):
                driver = driver_name
            case Err(error):
                click.echo(str(error))
                return
    converted_filters: list[int | str] = [
        int(f) if f.isnumeric() else f for f in filters
    ]
    loaded_dbs: Iterable[Database] = map(
        cantools.database.load_file,  # type: ignore[arg-type]
        collect_databases(*databases),
    )
    asyncio.run(
        _canviewer(
            channel,
            driver,
            loaded_dbs,
            ignore_unknown_messages,
            converted_filters,
            single_message=single_message,
            record_signals=record_signals,
            plot_signals=plot_signals,
            inline=inline,
            snapshot_type=snapshot_format,
            id_pattern=id_pattern,
            mask=int(mask, 16),
            always_show_value=always_show_value,
        )
    )


def apply_substitution_pattern(
    can_id: int,
    pattern: str,
    substitute: str,
) -> int:
    """
    Replaces leading `pattern` in `can_id` by substitute.
    pattern and substitute should be passed as hex string, without `0x`.
    Wildcard '*' may be used to match any digit.
    """
    assert len(pattern) == len(substitute)
    can_id_as_str = f"{can_id:08x}"
    n = len(pattern)
    chunk = list(can_id_as_str[:n])
    match = "".join([chunk[i] if c == "*" else c for i, c in enumerate(pattern)])
    if not can_id_as_str.startswith(match):
        return can_id
    header = "".join([chunk[i] if c == "*" else c for i, c in enumerate(substitute)])
    modified_id = header + can_id_as_str[n:]
    return int(modified_id, base=16)


@click.command()
@click.argument("database", type=str)
@click.option(
    "-c",
    "--channel",
    default="can0",
    type=str,
    help="Name of the CAN channel to monitor",
)
@click.option(
    "-sp",
    "--substitute-prefix",
    default=None,
    type=str,
    help="A replace pattern to apply at the beginning of CAN IDs",
)
@click.option(
    "-l",
    "--log-level",
    default="ERROR",
    type=click.Choice(list(logging._nameToLevel), case_sensitive=False),
    help="Log level to apply",
)
# JSON model config options
@click.option(
    "-a",
    "--accumulate",
    is_flag=True,
    help=(
        "When passed, stores all passed values in the message JSON file "
        "instead of only the last one"
    ),
)
@click.option(
    "-d",
    "--diff",
    is_flag=True,
    help=(
        "In accumulate mode, only appends the values that changed "
        "instead of appending everything. "
        "If timestamping is on the new timestamp is always shown"
    ),
)
@click.option(
    "-o",
    "--output-folder",
    type=str,
    help=(
        "If passed, the temp folder for JSON files will be created in this location.\n"
        "If not, it will be created somewhere in /tmp"
    ),
)
@click.option(
    "-p",
    "--preserve-files",
    is_flag=True,
    help=(
        "Whether the temp folder and its JSON files should be deleted on exit.\n"
        "Disabled by default."
    ),
)
@click.option(
    "-t",
    "--timestamps",
    is_flag=True,
    help=(
        """
        Writes last reception timestamp in the JSON file for message,
        in a LAST_RECEIVED key.
        """
    ),
)
@click.option(
    "-abs",
    "--absolute-time",
    is_flag=True,
    help=(
        """
        Uses absolute time instead of relative for timestamps.
        """
    ),
)
@click.option(
    "-raw",
    "--show-raw-data",
    is_flag=True,
    help="Includes raw data (ID and payload) in the generated JSON",
)
@click.option(
    "-no-rx",
    "--disable-rx",
    is_flag=True,
    help=(
        "Does not monitor RX messages and uses the model only to send message. "
        "Can be used when trying to override RX messages, "
        "as they are by default not overwritable when being monitored"
    ),
)
def canviewer_jsonify(
    *,
    database: str,
    channel: str,
    substitute_prefix: str | None,
    log_level: str,
    accumulate: bool,
    diff: bool,
    output_folder: str,
    preserve_files: bool,
    timestamps: bool,
    absolute_time: bool,
    show_raw_data: bool,
    disable_rx: bool,
) -> None:
    """
    database: Path to the database to JSONify
    """
    if substitute_prefix is not None:
        try:
            l_pattern, l_substitute = substitute_prefix.split(":")
            if len(l_pattern) != len(l_substitute):
                click.error("Pattern and substitute must have the same length")
                return
        except ValueError:
            click.echo("Substitution pattern must be passed as pattern:substitute")
            return
    else:
        l_pattern = None

    logging.basicConfig(
        level=logging._nameToLevel[log_level],
        format="{asctime}: {levelname:<7}: {threadName:<20}: {message}",
        style="{",
    )
    logging.getLogger("inotify").setLevel(logging.ERROR)

    # forcinf accumulate if diff was passed
    if diff:
        accumulate = True

    config = ModelConfig(
        accumulate=accumulate,
        diff=diff,
        target_folder=output_folder,
        preserve_files=preserve_files,
        enable_timestamping=timestamps,
        relative_time=not absolute_time,
        include_raw_data=show_raw_data,
    )
    try:
        can_db = cantools.database.load_file(database)
    except FileNotFoundError:
        rich.print(f"[red]: File does not exist: {database}")
        return

    def report_error(message_name: str, exc: Exception) -> None:
        rich.print(f"[red] Values for message {message_name} are incorrect: {exc}")

    assert isinstance(can_db, Database)
    model = JsonModel(can_db, config=config)
    with can.interface.Bus(interface="socketcan", channel=channel) as bus:
        with model.open() as tmp:
            rich.print(f"Path to model:\n[green]{tmp}")
            try:
                pyperclip.copy(tmp)
                rich.print("[green]> Path has been copied to your clipboard ! ")
            except Exception as exc:
                _logger.error(
                    "Failed to copy tmp path to clipboard: %s", exc, exc_info=True
                )
            model.start_inotify_watcher(bus, on_error=report_error)
            if disable_rx:
                input("Press any key to cleanup temp files and exit...")
                return

            rich.print("Use Ctrl + C to leave")
            while True:
                next_message = bus.recv()
                assert next_message is not None
                if l_pattern:
                    postprocessed_id = apply_substitution_pattern(
                        next_message.arbitration_id, l_pattern, l_substitute
                    )
                    next_message.arbitration_id = postprocessed_id
                assert next_message is not None  # value can only be None on timeout
                model.update_model(next_message)


@click.command
@click.argument("candump", type=Path)
@click.option(
    "-c",
    "--channel",
    default="can0",
    type=str,
    help="CAN channel on which to replay the dump",
)
@click.option(
    "-std",
    "--is-from-stdout",
    is_flag=True,
    help="Whether the CAN dump was piped from stdout or not",
)
def can_player(*, candump: Path, channel: str, is_from_stdout: bool) -> None:
    """
    Replays the passed candump.
    """
    if not candump.exists():
        click.echo(f"Could not find {candump}")
        sys.exit(1)
    try:
        messages = parse_candump(
            candump.read_text().split("\n"), is_stdout=is_from_stdout
        )
    except DumpParseError as exc:
        click.echo(f"Failed to parse candump: {exc}")
        sys.exit(1)
    asyncio.run(replay(messages, dest_channel=channel))
