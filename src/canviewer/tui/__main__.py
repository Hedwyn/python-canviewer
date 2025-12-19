"""
TUI entrypoint.
Usage: python -m canviewer.tui DATABASE_PATH
Multiple databases can be passed.

@date: 19.12.2025
@author: Baptiste Pestourie
"""

from __future__ import annotations

import json
import logging
import sys

from canviewer.tui._interface import Backend, CanViewer, DatabaseStore, WidgetDispatcher

logging.basicConfig(filename="tui.log", level=logging.INFO)
store = DatabaseStore.from_files(*sys.argv[1:])
backend = Backend(store, channel="vcan0", interface="socketcan")
dispatcher = WidgetDispatcher(store)
with open("widgets.json", "w+") as f:
    f.write(json.dumps((dispatcher.serialize_model()), indent=4, default=str))
viewer = CanViewer(backend=backend)
viewer.run()
