"""
Runtime utilities: provide convenience methods
to watch signal values and react on changes.

@author: Baptiste Pestourie
@date: 24.06.2026
"""

from __future__ import annotations

import asyncio
import time
import warnings
from asyncio import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, is_dataclass
from enum import Enum, auto
from functools import partial
from typing import (
    TYPE_CHECKING,
    NamedTuple,
    Protocol,
    Self,
    get_type_hints,
    overload,
)

from cantools.database.namedsignalvalue import NamedSignalValue
from typing_extensions import TypeForm

from canviewer import async_bus_poller, find_sound_default

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator

    from _typeshed import DataclassInstance
    from can.bus import BusABC
    from cantools.database.can import Database
    from cantools.database.can.signal import Signal

    from canviewer._jsonify import CanBasicTypes

type SignalValue = int | float | str


def iter_annotations[T](type_hint: TypeForm[object], annotation_type: type[T]) -> Iterator[T]:
    metadata = getattr(type_hint, "__metadata__", None)
    if metadata is None:
        return
    for annotation in metadata:
        if isinstance(annotation, annotation_type):
            yield annotation


def get_annotations[T](type_hint: TypeForm[object], annotation_type: type[T]) -> list[T]:
    return list(iter_annotations(type_hint, annotation_type=annotation_type))


def get_annotation[T](
    type_hint: TypeForm[object],
    annotation_type: type[T],
    *,
    strict: bool = False,
) -> T | None:
    annotations = get_annotations(type_hint, annotation_type)
    if not annotations:
        return None

    if strict and len(annotations) > 1:
        raise ValueError(f"Found more than one annotation of type {annotation_type} in {type_hint}")
    return annotations.pop()


def get_signal_map(node: DataclassInstance) -> dict[str, dict[str, SignalContainer[CanBasicTypes]]]:
    signal_map: dict[str, dict[str, SignalContainer[CanBasicTypes]]] = {}
    node_cls = node.__class__
    for field_name, type_hint in get_type_hints(node_cls, include_extras=True).items():
        subcls = getattr(node, field_name)
        if not (is_dataclass(subcls)):
            continue
        msg_name = get_annotation(type_hint, str, strict=True)
        assert msg_name is not None
        msg_map = signal_map.setdefault(msg_name, {})
        for subfield_name, hint in get_type_hints(subcls.__class__, include_extras=True).items():
            signal_container = getattr(subcls, subfield_name)
            if not isinstance(signal_container, SignalContainer):
                continue
            signal_name = get_annotation(hint, str, strict=True)
            assert signal_name is not None
            msg_map[signal_name] = signal_container
    return signal_map


async def run_dispatcher(
    bus: BusABC,
    database: Database,
    signal_map: dict[str, dict[str, SignalContainer[CanBasicTypes]]],
    mask: int = 0xFFFF_FFFF,
) -> None:
    async for next_msg in async_bus_poller(bus):
        can_id = next_msg.arbitration_id & mask
        try:
            target_msg = database.get_message_by_frame_id(can_id)
        except KeyError:
            continue
        message_container = signal_map.get(target_msg.name)
        if message_container is None:
            warnings.warn(
                f"Received a message {target_msg.name} that's unknown in the auto-generated code. "
                "Either the auto-generated code is out of sync or you are misuing this function",
                stacklevel=2,
            )
            continue

        decoded = target_msg.decode(bytes(next_msg.data))
        assert isinstance(decoded, dict)

        for signal_name, value in decoded.items():
            signal_container = message_container.get(signal_name)
            if signal_container is None:
                warnings.warn(
                    f"Received a signal {target_msg.name}:{signal_name} "
                    "that's unknown in the auto-generated code. "
                    "Either the auto-generated code is out of sync or you are misuing the function",
                    stacklevel=2,
                )
                continue
            if isinstance(value, NamedSignalValue):
                value = value.name  # noqa: PLW2901
            signal_container.update(value)


@asynccontextmanager
async def monitor[T: DataclassInstance](
    bus: BusABC,
    database: Database,
    node: T,
    mask: int = 0xFFFF_FFFF,
) -> AsyncGenerator[T]:
    signal_map = get_signal_map(node)
    with bus:
        dispatcher_task = asyncio.create_task(run_dispatcher(bus, database, signal_map, mask=mask))
        yield node
        dispatcher_task.cancel()


class Tolerance(NamedTuple):
    scale: float = 1.0
    offset: float = 0.0

    @classmethod
    def from_values(
        cls,
        scale: float | None = None,
        offset: float | None = None,
    ) -> Self:
        return cls(scale or 1.0, offset or 0.0)

    def apply(self, value: float, *, upper_bound: bool = False) -> float:
        sign = 1 if upper_bound else -1
        total_offset = self.offset + self.scale * value
        return value + total_offset * sign

    def get_interval(self, value: float) -> tuple[float, float]:
        return self.apply(value), self.apply(value, upper_bound=True)

    def almost_equal(self, expected: float, obtained: float) -> bool:
        lower_bound, upper_bound = self.get_interval(expected)
        return obtained >= lower_bound and obtained <= upper_bound


class Condition(Protocol):
    def is_met(self, value: SignalValue) -> bool: ...


@dataclass
class Equal:
    expected: SignalValue

    def is_met(self, value: SignalValue) -> bool:
        return self.expected == value


@dataclass
class AlmostEqual:
    expected: int | float
    tolerance: Tolerance = field(default_factory=Tolerance)

    def is_met(self, value: SignalValue) -> bool:
        if not isinstance(value, (int, float)):
            return False
        return self.tolerance.almost_equal(self.expected, value)


@dataclass
class DifferentThan:
    value: SignalValue

    def is_met(self, value: SignalValue) -> bool:
        return self.value != value


@dataclass
class LesserThan:
    value: int | float
    strict: bool = False

    def is_met(self, value: SignalValue) -> bool:
        if not isinstance(value, (int, float)):
            return False
        return value < self.value if self.strict else value <= self.value


@dataclass
class Greater:
    value: int | float
    strict: bool = False

    def is_met(self, value: SignalValue) -> bool:
        if not isinstance(value, (int, float)):
            return False
        return value > self.value if self.strict else value >= self.value


class Waiter[T](NamedTuple):
    """
    A simple handle on top of a future
    specifiying under which conditions the future should be triggered.
    If `condition` is given, `future` shall only be triggered
    when the signal value is equal to condition.
    If a `tolerance` is given, almost_equal will be used
    instead of equal using the given tolerance.
    """

    future: Future[T]
    condition: Condition | None = None


@dataclass
class SignalContainer[T: SignalValue]:
    value: T
    struct: Signal
    last_seen: float | None = None

    _watchers: list[Waiter[T]] = field(default_factory=list)
    _hooks: set[Callable[[Self], object]] = field(default_factory=set)

    def __repr__(self) -> str:
        return str(self.value)

    def __hash__(self) -> int:
        return hash((id(self), self.struct))

    @classmethod
    def from_signal(cls, signal: Signal) -> SignalContainer[SignalValue]:
        return SignalContainer(find_sound_default(signal), struct=signal)

    @classmethod
    def get_factory(
        cls,
        signal: Signal,
        expected_type: type[T],
    ) -> Callable[[], SignalContainer[T]]:
        default_value = find_sound_default(signal)
        assert isinstance(default_value, expected_type)
        return partial(SignalContainer, default_value, signal)

    def update(self, new_value: T, timestamp: float | None = None) -> None:
        timestamp = timestamp or time.time()
        self.last_seen = timestamp
        self.value = new_value

        done_watchers: list[Waiter[T]] = []
        for waiter in self._watchers:
            future, condition = waiter
            is_met = condition is None or condition.is_met(new_value)
            if is_met:
                future.set_result(new_value)
                done_watchers.append(waiter)
        for waiter in done_watchers:
            self._watchers.remove(waiter)

    def wait_condition(
        self,
        condition: Condition | None,
        future: Future[T] | None = None,
    ) -> Future[T]:
        future = future or Future()
        if condition is not None and condition.is_met(self.value):
            future.set_result(self.value)
        else:
            waiter = Waiter(future, condition)
            self._watchers.append(waiter)
        return future

    def wait_next(self, future: Future[T] | None = None) -> Future[T]:
        return self.wait_condition(None, future)

    def wait_change(self, future: Future[T] | None = None) -> Future[T]:
        condition = DifferentThan(self.value) if self.last_seen is not None else None
        return self.wait_condition(condition, future)

    async def wait_until(
        self,
        value: T,
        future: Future[T] | None = None,
    ) -> Future[T]:
        """
        Example
        -------
        converter.state.wait_until("DC_Ready")
        """
        return self.wait_condition(Equal(value), future)

    async def wait_until_approximately(
        self,
        value: T,
        future: Future[T] | None = None,
        margin_absolute: float | None = None,
        margin_relative: float | None = None,
    ) -> Future[T]:
        """
        Example
        -------
        converter.state.wait_until("DC_Ready")
        """
        tolerance = Tolerance.from_values(margin_relative, margin_absolute)
        if not isinstance(value, (int, float)):
            raise TypeError("Signal is not numeric, cannot use approximation")
        return self.wait_condition(AlmostEqual(value, tolerance), future)

    @overload  # type: ignore[override]
    def __eq__(self, other: SignalContainer[SignalValue]) -> bool: ...
    @overload
    def __eq__(self, other: SignalValue) -> Future[T]: ...
    def __eq__(self, other: object) -> bool | Future[T]:
        if isinstance(other, SignalContainer):
            return object.__eq__(self, other)
        assert isinstance(other, (int, float, str))
        return self.wait_condition(Equal(other))

    @overload  # type: ignore[override]
    def __ne__(self, other: SignalContainer[SignalValue]) -> bool: ...
    @overload
    def __ne__(self, other: SignalValue) -> Future[T]: ...
    def __ne__(self, other: object) -> bool | Future[T]:
        if isinstance(other, SignalContainer):
            return object.__ne__(self, other)
        assert isinstance(other, (int, float, str))
        return self.wait_condition(DifferentThan(other))

    def __lt__(self, other: float) -> Future[T]:
        return self.wait_condition(LesserThan(other, strict=True))

    def __le__(self, other: float) -> Future[T]:
        return self.wait_condition(LesserThan(other, strict=False))

    def __gt__(self, other: float) -> Future[T]:
        return self.wait_condition(Greater(other, strict=True))

    def __ge__(self, other: float) -> Future[T]:
        return self.wait_condition(Greater(other, strict=False))


class SendPolicy(Enum):
    """
    Whether a given message should be sent and how it should be handled.

    INACTIVE: message's not being sent at all (e.g., RX message),
    explicit sends will trigger a warning.

    EXPLICIT: message will only be sent when explicity called by the script,
    main difference with `INACTIVE` is that it won't issue a warning.

    CYCLIC: sends the message periodically, according the cycle time defined
    in the message struct.

    ON_CHANGE: sends the message only when one of the signal value is changed.
    """

    INACTIVE = auto()
    EXPLICIT = auto()
    CYCLIC = auto()
    ON_CHANGE = auto()
