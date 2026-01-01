"""
Main entrypoint to start the TUI.

@date: 28.12.2025
@author: Baptiste Pestourie
"""

from __future__ import annotations

import json
import logging
import platform

import click

from canviewer._monitor import get_platform_default_channel, get_platform_default_driver
from canviewer.tui._interface import Backend, CanViewer, DatabaseStore, WidgetDispatcher


@click.command()
@click.argument("databases", nargs=-1, type=str)
@click.option("-c", "--channel", type=str, help="CAN channel to run on", default=None)
@click.option("-i", "--interface", type=str, help="CAN channel to run on", default=None)
def canviewer_tui(
    *, databases: tuple[str], channel: str | None, interface: str | None
) -> None:
    """
    Starts the Terminal User Interface.
    """
    interface = interface or get_platform_default_driver().unwrap()
    channel = channel or get_platform_default_channel().unwrap()
    logging.basicConfig(filename="tui.log", level=logging.INFO)
    store = DatabaseStore.from_files(*databases)
    backend = Backend(store, channel=channel, interface=interface)
    dispatcher = WidgetDispatcher(store)
    with open("widgets.json", "w+") as f:
        f.write(json.dumps((dispatcher.serialize_model()), indent=4, default=str))
        viewer = CanViewer(backend=backend)
        viewer.run()
