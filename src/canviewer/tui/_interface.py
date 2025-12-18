"""
A TUI allowing to set CAN parameters using widgets.
Creates an appropriate widget based on the signal characteristics.

@date: 18.12.2025
@author: Baptiste Pestourie
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager, NamedTuple, Self

import cantools
from cantools.database.can.signal import Signal
from cantools.database.namedsignalvalue import NamedSignalValue

# textual imports
from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.reactive import Reactive, reactive
from textual.widget import Widget
from textual.widgets import (
    Collapsible,
    Input,
    Label,
    RadioButton,
    RadioSet,
)

from canviewer._monitor import CanDatabase, CanFrame, CanMonitor, CanTypes

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

    from textual._path import CSSPathType
    from textual.driver import Driver

_logger = logging.getLogger(__name__)


type JsonLike = dict[str, int | str | float | None | JsonLike]


class SignalWidget(Widget):
    """
    A dynamic widget for signal value controllers that re-composes itself
    when certain parameters are changed (e.g., message direction).
    """

    is_tx: Reactive[bool] = reactive(True, recompose=True)

    def compose(self) -> ComposeResult:
        """
        Defaults to Input for TX messages and Label for RX
        """
        is_tx = self.is_tx
        if is_tx:
            yield Input(value="0")
        else:
            yield Label(content="0")


@dataclass
class NamedDatabase:
    """
    Adds a label to cantools database.
    Standard use to to load using NamedDatabase.load_from_file(),
    which will keep the filename as identifier.
    """

    name: str
    database: CanDatabase

    @property
    def nodes(self) -> list[str]:
        """
        Labels given to the CAN nodes communicating on the bus
        using the given database.
        This will define the direction of messages based on the configured producer.
        """
        return [node.name for node in self.database.nodes]

    @property
    def messages(self) -> list[CanFrame]:
        """
        All the messages declared in the database.
        """
        return self.database.messages

    def get_message_by_name(self, name: str) -> CanFrame | None:
        """
        Returns
        -------
        CanFrame | None
            The frame registered under that name or None if it does not exist.
        """
        try:
            return self.database.get_message_by_name(name)
        except KeyError:
            return None

    @classmethod
    def load_from_file(cls, path: str | Path) -> Self:
        """
        Loads the database from the given `path`.
        """
        name = Path(path).stem
        loaded_db = cantools.database.load_file(path)
        assert isinstance(loaded_db, CanDatabase)
        return cls(
            name=name,
            database=loaded_db,
        )


@dataclass
class TUIConfig:
    """
     Main config dataclass for the TUI itself.
     Allows configuring the way the TUI is laid out and rendered.

     Parameters
     ----------
     collapse_database
         Wraps each database in a collapsible
         (collapsed on startup).

    collapse_messages
         Wraps each message in a collapsible
         (collapsed on startup).

    """

    collapse_database: bool = False
    collapse_messages: bool = True


@dataclass(frozen=True, eq=True)
class SignalID:
    """
    The identifier for a given emssage, based on its database, message and
    signal name combination.
    Database is optional but not passing it might creat conflict if duplicate
    message/signal pairs exist across databases.
    """

    message: str
    signal: str
    db_name: str | None = None

    @property
    def identifier(self) -> str:
        """
        Returns
        -------
        str
            A single-string identifier for this signal ID.
        """
        if self.db_name:
            return f"{self.db_name}-{self.message}-{self.signal}"
        return f"{self.message}-{self.signal}"

    @property
    def query_key(self) -> str:
        """
        Returns
        -------
        str
           The formatted identifier so it can be used
           directly as query key.
        """
        return f"#{self.identifier}"


@dataclass
class SignalProperties:
    """
    Modelizes a given signal properties so
    that an appropriate widget can be built.
    """

    signal_type: type[int] | type[float] | type[str] = int
    min_value: float | None = None
    max_value: float | None = None
    choices: tuple[str | int, ...] | None = None
    senders: tuple[str, ...] = ()


class NamedSignalWidget(NamedTuple):
    """
    Stores a signal widget together with its identifier and properties.
    """

    signal_id: SignalID
    widget: Widget
    properties: SignalProperties


# placeholder for future customization feature
type CustomRules = object


class WidgetDispatcher:
    """
    Given a set of signal/message properties and custom constraints,
    dispatches an appropriate Widget to control and/or represent
    the signal in the interface.
    """

    def __init__(
        self, *databases: NamedDatabase, custom_rules: CustomRules | None = None
    ) -> None:
        self._databases = databases
        if custom_rules is not None:
            raise NotImplementedError("Custom rules not available yet")
        self.custom_rules = custom_rules

    def _find_message(self, message_name: str) -> CanFrame:
        """
        Looks for message `message_name` in all registered databases.
        """
        for db in self._databases:
            if (msg := db.get_message_by_name(message_name)) is not None:
                return msg
        raise ValueError(
            f"Message named {message_name} was queried internally "
            "but not found in any DB"
        )

    @cache
    def extract_signal_properties(
        self, signal: Signal, *senders: str
    ) -> SignalProperties:
        """
        Builds the signal properties out of a cantools Signal object.
        `senders` should provide the node names that emit the given signal.
        """
        signal_type: type[int | str] = int
        if signal.choices:
            choices: None | list[int] | list[str] = list(signal.choices)
            assert choices
            # Note: assumption here is that choices all have the same type
            # assert all([(type(c) is type(choices[0]) for c in choices[1:])])
            if isinstance(choices[0], NamedSignalValue):
                signal_type = str
                choices = [str(c) for c in choices]
        else:
            choices = None
        min_value = self._get_sound_minimum(signal)
        max_value = self._get_sound_maximum(signal)

        return SignalProperties(
            signal_type,
            min_value,
            max_value,
            tuple(choices) if choices is not None else (),
            senders,
        )

    def _get_sound_minimum(self, signal: Signal) -> float | None:
        """
        Builds a default minimum for `signal` if possible.
        For signal that define an explicit one, returns it immediately.
        Otherwise, define the minimum based on signal size and sign.
        """
        if signal.choices:
            return None
        if signal.minimum is not None:
            return signal.minimum
        if not signal.is_signed:
            return 0
        exponent = signal.length - 1
        return -(2**exponent)

    def _get_sound_maximum(self, signal: Signal) -> float | None:
        """
        Builds a default maximum for `signal` if possible.
        For signal that define an explicit one, returns it immediately.
        Otherwise, define the maximum based on signal size and sign.
        """
        if signal.choices:
            return None
        if signal.maximum:
            return signal.maximum
        exponent = signal.length
        if signal.is_signed:
            exponent -= 1
        return (2**exponent) - 1

    def serialize_model(self) -> JsonLike:
        """
        Exports a serialized version of the signal properties.
        """
        output: JsonLike = {}
        for db in self._databases:
            for message in db.messages:
                for signal in message.signals:
                    key = f"{message.name}:{signal.name}"
                    output[key] = {
                        "properties": asdict(
                            self.extract_signal_properties(signal, *message.senders)
                        ),
                    }

        return output

    def dispatch(
        self, message_name: str, signal_name: str, value: CanTypes, is_tx: bool = True
    ) -> NamedSignalWidget:
        """
        Dispatches an appropriate Widget to repsent the given signal.
        """
        frame = self._find_message(message_name)
        signal = frame.get_signal_by_name(signal_name)
        properties = self.extract_signal_properties(signal, *frame.senders)
        signal_id = SignalID(
            message=message_name,
            signal=signal.name,
        )
        widget = SignalWidget(id=signal_id.identifier)
        return NamedSignalWidget(
            signal_id=signal_id, widget=widget, properties=properties
        )


class Backend:
    """
    Manages all the internal operations of the application.
    """

    def __init__(self, *databases: str, **can_params: str | int) -> None:
        self._databases = databases
        self._monitor: CanMonitor | None = None
        self.can_params = can_params

    @cached_property
    def databases(self) -> tuple[NamedDatabase, ...]:
        """
        Lazily loads all databases on first access.
        """
        return tuple((NamedDatabase.load_from_file(db) for db in self._databases))

    @property
    def monitor(self) -> CanMonitor:
        """
        Monitoring interface over the CAN bus.
        """
        if self._monitor is None:
            raise RuntimeError("No monitor attached")
        return self._monitor

    def start(self, loop: AbstractEventLoop | None = None) -> None:
        """
        Starts the CAN monitoring loop.
        """
        loop = loop or asyncio.get_running_loop()
        assert loop is not None, "Not running in async context"
        loop.create_task(self._watch_monitor())
        _logger.info("Created task")

    async def _watch_monitor(self) -> None:
        """
        Main monitoring loop, only logs the CAN messages for now.
        """
        _logger.info("Backend starting with params %s", self.can_params)
        with can.Bus(**self.can_params) as bus:  # type: ignore[arg-type]
            self._monitor = CanMonitor(bus, *(db.database for db in self.databases))
            while True:
                msg = await self.monitor.queue.get()
                _logger.info("%s", msg)


class CanViewer(App[None]):
    """
    Main TUI application.
    Given a backend monitoring a set of CAN databases,
    builds a UI dynamically to control all signals from these databases.
    """

    def __init__(
        self,
        backend: Backend,
        dispatcher: WidgetDispatcher | None = None,
        config: TUIConfig | None = None,
        driver_class: type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css, ansi_color)
        self._config = config or TUIConfig()
        self._backend = backend
        self._dispatcher = dispatcher or WidgetDispatcher(*backend.databases)
        self._producers: dict[
            str, str | None
        ] = {}  # maps each database to its selected producer
        self._signal_properties: dict[
            SignalID, SignalProperties
        ] = {}  # maps each SignalWidget ID to its properties

    def on_mount(self) -> None:
        """
        Starts the backend and tweaks widgets.
        """
        self.call_after_refresh(self.ensure_radioset_defaults)
        self.ensure_radioset_defaults()
        self._backend.start()

    def get_selected_producer(self, database_name: str) -> str | None:
        """
        Returns the producer node that's currently selected in the UI.
        """

        try:
            radio_set_id = f"#{database_name}-producer"
            _logger.info("Querying %s", radio_set_id)
            node_radio_set = self.query_one(radio_set_id, RadioSet)

        except NoMatches:
            _logger.info("No matches")
            return None
        if (button := node_radio_set.pressed_button) is not None:
            return button.name
        return None

    def ensure_radioset_defaults(self) -> None:
        """
        Ensure every RadioSet has a selected value.
        """
        for radioset in self.query(RadioSet):
            _logger.info("radio set %s", radioset)
            buttons = radioset.query(RadioButton).results()
            for idx, button in enumerate(buttons):
                button.value = idx == 0

    def _compose_message_widgets(
        self, message: CanFrame, is_tx: bool = True
    ) -> ComposeResult:
        """
        Yields
        ------
        Widget
            All the signal widgets for a given CAN message.
        """
        for signal in message.signals:
            signal_id, widget, properties = self._dispatcher.dispatch(
                message.name, signal.name, 0, is_tx=is_tx
            )
            title = f"{signal.name:25}"
            yield Label(content=title)
            yield widget
            self._signal_properties[signal_id] = properties

    def compose(self) -> ComposeResult:
        """
        Builds the main UI layout.
        """

        def db_collapsible() -> ContextManager:
            return (
                Collapsible()
                if self._config.collapse_database
                else contextlib.nullcontext()
            )

        def msg_collapsible() -> ContextManager:
            return (
                Collapsible(title=msg.name)
                if self._config.collapse_messages
                else contextlib.nullcontext()
            )

        for db in self._backend.databases:
            # showing nodes
            default_node: str | None = None
            if db.nodes:
                default_node = db.nodes[0]
                yield Label(content="Producer")
                radio_set_id = f"{db.name}-producer"
                radio_set = RadioSet(*db.nodes, id=radio_set_id)
                yield radio_set
                _logger.info("%s", radio_set.pressed_button)
                _logger.info("%s", self.get_selected_producer(db.name))
            self._producers[db.name] = default_node

            with db_collapsible():
                for msg in db.messages:
                    is_tx = default_node is None or default_node in msg.senders
                    assert isinstance(default_node, str)
                    _logger.info(
                        "senders=%s, default_node=%s, is_tx %s",
                        msg.senders,
                        default_node,
                        is_tx,
                    )
                    with msg_collapsible():
                        yield from self._compose_message_widgets(msg)

    # --- Handlers on signal widgets interactions --- #
    @on(Input.Submitted)
    def on_signal_value_edited(self, event: Input.Submitted) -> None:
        _logger.info("Input changed: %s", event)

    @on(RadioSet.Changed)
    def on_producer_changed(self, event: RadioSet.Changed) -> None:
        """
        Called when the user selects a new producer in the UI.
        """
        _logger.info("Radio set changed")
        assert event.radio_set.id is not None, "ID should be set at init time"
        name = event.radio_set.id.replace("-producer", "")
        new_producer = str(event.pressed.label)
        _logger.info("Producer changed for %s: %s", name, new_producer)
        self.change_message_direction(new_producer)

    def change_message_direction(self, new_producer: str) -> None:
        """
        When a new producer is selected,
        changes the direction of signal widgets accordingly.
        """
        for signal_id, properties in self._signal_properties.items():
            widget = self.query_one(signal_id.query_key, SignalWidget)

            assert isinstance(widget, SignalWidget)
            is_tx = new_producer in properties.senders
            widget.is_tx = is_tx


if __name__ == "__main__":
    import json

    import can

    logging.basicConfig(filename="tui.log", level=logging.INFO)

    _logger.info("Hello world")
    backend = Backend("ADM_PC_BP25.kcd", channel="vcan0", interface="socketcan")
    dispatcher = WidgetDispatcher(*backend.databases)
    with open("widgets.json", "w+") as f:
        f.write(json.dumps((dispatcher.serialize_model()), indent=4, default=str))
    viewer = CanViewer(backend=backend)
    viewer.run()
