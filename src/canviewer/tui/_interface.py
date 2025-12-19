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
from functools import cache
from typing import TYPE_CHECKING, Callable, ContextManager, NamedTuple, Self

import can
from cantools.database.can.signal import Signal
from cantools.database.namedsignalvalue import NamedSignalValue
from exhausterr import Err, Ok

# textual imports
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import Reactive, reactive
from textual.widgets import (
    Collapsible,
    Input,
    Label,
    RadioButton,
    RadioSet,
)

from canviewer._monitor import (
    CanFrame,
    CanMonitor,
    CanTypes,
    DatabaseStore,
    DecodedMessage,
    NamedDatabase,
)

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop, Task

    from textual._path import CSSPathType
    from textual.driver import Driver

_logger = logging.getLogger(__name__)


type JsonLike = dict[str, int | str | float | None | JsonLike]


@dataclass
class SignalValueEdited(Message):
    widget: SignalWidget
    value: int | str | float


@dataclass
class SignalValueChanged(Message):
    signal_id: SignalID
    value: CanTypes


class SignalWidget(Container):
    """
    A dynamic widget for signal value controllers that re-composes itself
    when certain parameters are changed (e.g., message direction).
    """

    label: Reactive[str] = reactive("", recompose=True)
    is_tx: Reactive[bool] = reactive(True, recompose=True)
    current_value: Reactive[CanTypes] = reactive("0", recompose=True)
    period: Reactive[float | None] = reactive(None)

    @on(Input.Submitted)
    def on_signal_value_edited(self, event: Input.Submitted) -> None:
        _logger.info("SignalWidget Input changed: %s %s", event, event.input)
        self.post_message(SignalValueEdited(widget=self, value=event.value))

    def compose(self) -> ComposeResult:
        """
        Defaults to Input for TX messages and Label for RX
        """
        is_tx = self.is_tx
        value = self.current_value
        formatted_value = hex(value) if isinstance(value, int) else str(value)
        with Horizontal():
            yield Label(content=f"{self.label:25}")
            if is_tx:
                yield Input(value=formatted_value)
            else:
                yield Label(content=formatted_value)


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

    db_name: str
    message: str
    signal: str

    def __str__(self) -> str:
        return self.identifier

    @classmethod
    def from_identifier(cls, identifier: str) -> Self:
        return cls(*identifier.split("-"))

    @property
    def identifier(self) -> str:
        """
        Returns
        -------
        str
            A single-string identifier for this signal ID.
        """
        return f"{self.db_name}-{self.message}-{self.signal}"

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

    def find_sound_default(self: SignalProperties) -> CanTypes:
        """
        Finds a reasonable default value of a given decalred CAN signal.
        """
        minimum = self.min_value
        maximum = self.max_value
        if minimum is not None:
            if maximum:
                return int((minimum + maximum) / 2)
            return minimum
        if self.choices is not None:
            assert len(self.choices) > 0
            first_option = self.choices[0]
            if isinstance(first_option, NamedSignalValue):
                return str(first_option)
            return first_option
        return 0


class NamedSignalWidget(NamedTuple):
    """
    Stores a signal widget together with its identifier and properties.
    """

    signal_id: SignalID
    widget: SignalWidget
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
        self, database_stores: DatabaseStore, custom_rules: CustomRules | None = None
    ) -> None:
        self._database_store = database_stores
        if custom_rules is not None:
            raise NotImplementedError("Custom rules not available yet")
        self.custom_rules = custom_rules

    def _find_message_and_db(self, message_name: str) -> tuple[CanFrame, NamedDatabase]:
        """
        Looks for message `message_name` in all registered databases
        and returns both the message and the database in which it's declared.
        """
        for db in self._database_store:
            if (msg := db.get_message_by_name(message_name)) is not None:
                return msg, db
        raise ValueError(
            f"Message named {message_name} was queried internally "
            "but not found in any DB"
        )

    def _find_message(self, message_name: str) -> CanFrame:
        """
        Looks for message `message_name` in all registered databases.
        """
        return self._database_store.find_message_and_db(message_name)[0]

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
        for db in self._database_store:
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
        frame, db = self._database_store.find_message_and_db(message_name)
        signal = frame.get_signal_by_name(signal_name)
        properties = self.extract_signal_properties(signal, *frame.senders)
        signal_id = SignalID(
            db_name=db.name,
            message=message_name,
            signal=signal.name,
        )
        widget = SignalWidget(id=signal_id.identifier)
        value = properties.find_sound_default()
        widget.current_value = value
        _logger.info("Setting signal %s to value %s", signal.name, value)
        return NamedSignalWidget(
            signal_id=signal_id, widget=widget, properties=properties
        )


type MessageCallback = Callable[[DecodedMessage], None]


class Backend:
    """
    Manages all the internal operations of the application.
    """

    def __init__(
        self,
        database_store: DatabaseStore | None = None,
        **can_params: str | int,
    ) -> None:
        self._database_store = database_store or DatabaseStore()
        self._monitor: CanMonitor | None = None
        self._message_callbacks: list[MessageCallback] = []
        self.can_params = can_params
        self._value_store: dict[SignalID, dict[str, CanTypes]] = {}
        self._messages: dict[CanFrame, dict[str, CanTypes]] = {}

        self._periodic_messages: dict[CanFrame, Task | None] = {}

    def is_set_as_periodic(self, frame: CanFrame) -> bool:
        return frame in self._periodic_messages

    @property
    def monitor(self) -> CanMonitor:
        """
        Monitoring interface over the CAN bus.
        """
        if self._monitor is None:
            raise RuntimeError("No monitor attached")
        return self._monitor

    @property
    def database_store(self) -> DatabaseStore:
        """
        The collection of databases defining the CAN messages
        monitored by this backend.
        """
        return self._database_store

    def add_message_callback(self, callback: MessageCallback) -> None:
        self._message_callbacks.append(callback)

    def start(self, loop: AbstractEventLoop | None = None) -> None:
        """
        Starts the CAN monitoring loop.
        """
        self.initialize_value_store()
        # adding periodic messages
        loop = loop or asyncio.get_running_loop()

        assert loop is not None, "Not running in async context"
        loop.create_task(self._watch_monitor())

    def _start_senders(self, loop: AbstractEventLoop | None = None) -> None:
        """
        Starts all periodic messages senders.
        """
        loop = loop or asyncio.get_running_loop()
        for msg in self.database_store.iter_periodic_messages():
            self._periodic_messages[msg] = loop.create_task(
                self._send_periodic_message_task(msg)
            )

    def initialize_value_store(self) -> None:
        for db in self._database_store:
            for msg in db.messages:
                msg_dict: dict[str, CanTypes] = {}
                for signal in msg.signals:
                    signal_id = SignalID(db.name, msg.name, signal.name)
                    self._value_store[signal_id] = msg_dict
                    self._messages[msg] = msg_dict
                    # TODO: compute a proper default
                    msg_dict[signal_id.signal] = 0

    def update_signal_value(
        self, signal_id: SignalID, value: CanTypes, send_now: bool = False
    ) -> None:
        """
        Updates the stored value for a given signal internally.
        """
        msg_dict = self._value_store[signal_id]
        msg_dict[signal_id.signal] = value
        if not send_now:
            return
        frame = self._database_store.find_message(signal_id.message)
        # TODO: create proper dedicated object
        payload = can.Message(
            arbitration_id=frame.frame_id, data=frame.encode(msg_dict)
        )
        _logger.info("Sending %s", payload)
        self.monitor.bus.send(payload)

    async def _send_periodic_message_task(
        self, frame: CanFrame, interval: float | None = None
    ) -> None:
        """
        Sends a given message at the given interval.
        """
        interval = interval or frame.cycle_time
        assert interval is not None, (
            "Cannot send a message without specifying an interval"
        )
        try:
            while True:
                msg_dict = self._messages[frame]
                payload = can.Message(
                    arbitration_id=frame.frame_id, data=frame.encode(msg_dict)
                )
                _logger.info("Sending %s", payload)
                self.monitor.bus.send(payload)
                await asyncio.sleep(interval)
        except Exception:
            _logger.error("Periodic sender failed", exc_info=True)

    async def _watch_monitor(self) -> None:
        """
        Main monitoring loop, only logs the CAN messages for now.
        """
        _logger.info("Backend starting with params %s", self.can_params)
        with can.Bus(**self.can_params) as bus:  # type: ignore[arg-type]
            self._monitor = CanMonitor(bus, self._database_store)
            while True:
                match await self.monitor.queue.get():
                    case Ok(decoded_msg):
                        _logger.info("Received: %s", decoded_msg)
                        for on_message in self._message_callbacks:
                            # TODO: catch
                            on_message(decoded_msg)
                    case Err(err):
                        _logger.error("Decoding failed: %s", err)


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
        self._dispatcher = dispatcher or WidgetDispatcher(backend._database_store)
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
        self._backend.add_message_callback(self.dispatch_new_messages_values)
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
            widget.label = title
            yield widget
            self._signal_properties[signal_id] = properties
            # checking that we can retrive
            assert (
                self._signal_properties.get(
                    SignalID.from_identifier(signal_id.identifier)
                )
                is not None
            )

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

        for db in self._backend.database_store:
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

    def dispatch_new_messages_values(self, decoded_msg: DecodedMessage) -> None:
        """
        Callback passed to the backend to display new signal values in the TUI when
        a message is received.
        """
        _logger.info("Dispatching messages values %s", decoded_msg)
        frame, db = self._backend.database_store.find_message_and_db(
            decoded_msg.frame_name
        )
        for signal in frame.signals:
            signal_id = SignalID(db.name, frame.name, signal.name)
            # Have to ignore the mux case
            if (value := decoded_msg.data.get(signal.name)) is None:
                continue
            self.post_message(SignalValueChanged(signal_id, value))
            widget = self.query_one(signal_id.query_key, SignalWidget)
            widget.current_value = value

    # --- Handlers on signal widgets interactions --- #
    @on(SignalValueChanged)
    def on_signal_value_changed(self, event: SignalValueChanged) -> None:
        _logger.info(
            "Modifying displayed signal %s value to %s", event.signal_id, event.value
        )

    @on(SignalValueEdited)
    def on_signal_value_edited(self, event: SignalValueEdited) -> None:
        _logger.info("Signal value changed")
        assert event.widget.id is not None, "All SignalValue widgets should have an ID"
        signal_id = SignalID.from_identifier(event.widget.id)
        properties = self._signal_properties[signal_id]
        converted_value = properties.signal_type(event.value)
        _logger.info("Updating signal value %s to %s", signal_id, converted_value)
        self._backend.update_signal_value(signal_id, converted_value, send_now=True)

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
