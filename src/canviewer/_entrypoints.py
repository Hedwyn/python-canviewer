"""
Application entrypoint(s) for this package.
CLI tools are based on click and will be installed
automatically as package scripts.

@date: 04.10.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations

# built-in
from typing import Iterator, Iterable
import can
import asyncio
import os
import cantools

# 3rd-party
import click
from rich.live import Live
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
    message_table = MessageTable(ignore_unknown_messages=ignore_unknown_messages)
    with can.Bus(interface=driver, channel=channel) as bus:
        with Live(auto_refresh=False) as live:
            backend = CanMonitor(bus, *databases)
            while True:  # Ctrl + C to leave
                message = await backend.queue.get()
                message_table.update(message)
                renderable_table = message_table.export()
                live.update(renderable_table)
                live.refresh()


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
    "-i",
    "--ignore-unknown-messages",
    is_flag=True,
    help="Hides messages that are not declared in one of your databases",
)
def canviewer(
    channel: str | None,
    driver: str | None,
    databases: Iterable[str],
    ignore_unknown_messages: bool,
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

    loaded_dbs: Iterable[Database] = map(
        cantools.database.load_file,  # type: ignore[arg-type]
        collect_databases(*databases),
    )
    asyncio.run(_canviewer(channel, driver, loaded_dbs, ignore_unknown_messages))