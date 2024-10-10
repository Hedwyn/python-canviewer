"""
Application entrypoint(s) for this package.
CLI tools are based on click and will be installed
automatically as package scripts.

@date: 04.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations

# built-in
from re import M
from typing import Iterator, Iterable
import can
import asyncio
import os
import cantools

# 3rd-party
import click
from rich.live import Live
from rich.console import Console
from cantools.database.can import Database
from exhausterr.results import Ok, Err

# Local
from ._monitor import (
    CanMonitor,
    get_platform_default_channel,
    get_platform_default_driver,
)
from ._console import MessageTable


async def _canviewer(
    channel: str,
    driver: str,
    databases: Iterable[Database],
    ignore_unknown_messages: bool,
    message_filters: Iterable[int | str],
    single_message: str | None = None,
    record_signals: list[str] = [],
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

    for message_signal in record_signals:
        if not message_table.start_plot(message_signal):
            click.echo(f"Invalid message signal: {message_signal}")
            return
    with can.Bus(interface=driver, channel=channel) as bus:
        with Live(console=console) as live:
            backend = CanMonitor(bus, *databases)
            while True:  # Ctrl + C to leave
                try:
                    message = await backend.queue.get()
                    message_table.update(message)
                    if single_message is not None:
                        renderable_table = message_table.export_single_message(
                            single_message
                        )
                        if renderable_table is None:
                            continue
                    else:
                        renderable_table = message_table.export()
                    live.update(renderable_table)
                except KeyboardInterrupt:
                    break

    csv_paths = message_table.export_plots_to_csv()
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
        "You shall pass your target signal as message_name.signal_name"
    ),
)
def canviewer(
    channel: str | None,
    driver: str | None,
    databases: Iterable[str],
    filters: Iterable[str],
    single_message: str | None,
    ignore_unknown_messages: bool,
    record_signals: list[str],
) -> None:
    """
    For every CAN ID found on the CAN bus,
    displays the data for the last message received.
    If the message is declared in one of the passed databases,
    shows the decoded data.
    """
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
        )
    )
