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
from canviewer.tui._entrypoint import canviewer_tui

if __name__ == "__main__":
    canviewer_tui()
