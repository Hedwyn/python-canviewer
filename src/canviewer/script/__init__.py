"""
Scripting utilities for canviewer.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
import warnings
from asyncio import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, is_dataclass
from functools import partial
from typing import (
    TYPE_CHECKING,
    Literal,
    NamedTuple,
    Protocol,
    Self,
    assert_never,
    cast,
    get_type_hints,
)

import cantools.database
from cantools.database.can import Database
from cantools.database.namedsignalvalue import NamedSignalValue
from typing_extensions import TypeForm

from canviewer import async_bus_poller, find_sound_default

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable, Iterator
    from pathlib import Path

    from _typeshed import DataclassInstance
    from can.bus import BusABC
    from cantools.database import Message
    from cantools.database.can.signal import Signal

    from canviewer._jsonify import CanBasicTypes

type SignalValue = int | float | str


def iter_annotations[T](type_hint: TypeForm[object], annotation_type: type[T]) -> Iterator[T]:
    """
    Iterates through annotations in an `Annotated` type hint.
    """
    metadata = getattr(type_hint, "__metadata__", None)
    if metadata is None:
        return
    for annotation in metadata:
        if isinstance(annotation, annotation_type):
            yield annotation


def get_annotations[T](type_hint: TypeForm[object], annotation_type: type[T]) -> list[T]:
    """
    Returns all annotations of type `T` found in the given type hint, as a list.
    """
    return list(iter_annotations(type_hint, annotation_type=annotation_type))


def get_annotation[T](
    type_hint: TypeForm[object],
    annotation_type: type[T],
    *,
    strict: bool = False,
) -> T | None:
    """
    Inspects the annotations in the `Annotated` fields of `type_hint`.
    Returns None if `type_hint` is not an Annotated or if no annotations
    of type `annotation_type` could be found.
    If strict is False, returns the first found annotation of type `annotation_type`,
    otherwise ensure that no more than one annotation of that type is there and raises
    ValueError otherwise.
    """
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


class Waiter[T](NamedTuple):
    future: Future[T]
    condition: T | None = None
    tolerance: Tolerance | None = None


@dataclass
class SignalContainer[T: SignalValue]:
    value: T
    struct: Signal
    last_seen: float | None = None

    _watchers: list[Waiter[T]] = field(default_factory=list)
    _hooks: set[Callable[[Self], object]] = field(default_factory=set)

    def __repr__(self) -> str:
        return str(self.value)

    @classmethod
    def from_signal(cls, signal: Signal) -> Self:
        default_value = cast("T", find_sound_default(signal))
        return cls(default_value, struct=signal)

    @classmethod
    def get_factory(cls, signal: Signal) -> Callable[[], Self]:
        default_value = cast("T", find_sound_default(signal))
        return partial(cls, default_value, signal)

    def update(self, new_value: T, timestamp: float | None = None) -> None:
        timestamp = timestamp or time.time()
        self.last_seen = timestamp
        self.value = new_value

        for future, condition, tolerance in self._watchers:
            is_met = condition is None
            if not is_met:
                if tolerance is None:
                    is_met = new_value == condition
                else:
                    assert isinstance(new_value, float)
                    assert isinstance(condition, float)
                    is_met = tolerance.almost_equal(condition, new_value)
            if is_met:
                future.set_result(new_value)
        self._watchers.clear()

    def wait_next(self, future: Future[T] | None = None) -> Future[T]:
        future = future or Future()
        self._watchers.append(Waiter(future))
        return future

    async def wait_until(
        self,
        condition: T,
        tolerance: Tolerance | None = None,
        future: Future[T] | None = None,
    ) -> Future[T]:
        """
        Example
        -------
        converter.state.wait_until("DC_Ready")
        """
        future = future or Future()
        waiter = Waiter(future, condition, tolerance)
        self._watchers.append(waiter)
        return future

    async def wait_until_approximately(
        self,
        condition: T,
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
        future = future or Future()
        waiter = Waiter(future, condition, tolerance)
        self._watchers.append(waiter)
        return future


type BuiltinNameConversions = Literal["camel_to_snake", "canonical"]


class NameConversionFn(Protocol):
    def __call__(self, name: str, *, is_type: bool = False) -> str: ...


@dataclass
class CodegenOptions:
    flatten_signals_tree: bool = False
    prefix_signal_names_with_msg: bool = False
    enforce_snakecase: bool = False
    name_conversion: NameConversionFn | BuiltinNameConversions = "camel_to_snake"
    inline_database: bool = False
    # formatting options below
    indent: str = " " * 4
    new_lines_after_cls: int = 2
    format_code: bool = False
    generate_main: bool = False

    def add_gap_after_cls(self) -> Iterator[str]:
        for _ in range(self.new_lines_after_cls):
            yield ""

    def convert_name(self, name: str, *, is_type: bool = False) -> str:
        if (conversion := self.get_conversion()) is not None:
            return conversion(name, is_type=is_type)
        return name

    def get_conversion(self) -> NameConversionFn:
        if isinstance((conversion := self.name_conversion), str):
            match conversion:
                case "camel_to_snake":
                    return camel_to_snake_case
                case "canonical":
                    return make_canonical
                case _ as unreachable:
                    assert_never(unreachable)

        return conversion


def sanity_checks(database: Database, config: CodegenOptions) -> None:
    if config.enforce_snakecase:
        raise NotImplementedError
    if not config.flatten_signals_tree or config.prefix_signal_names_with_msg:
        # nothing todo
        return
    signal_names: dict[str, str] = {}
    for msg in database.messages:
        for signal in msg.signals:
            if (duplicate := signal_names.get(signal.name)) is not None:
                raise ValueError(
                    f"Duplicated signal name {signal.name}, you are using `flatten_signals_tree`"
                    "Thus collisions are not permitted."
                    "Disable flattening or prefix message names to signal name using "
                    "`prefix_signal_names_with_msg`. "
                    f"Conflicting messages: {msg.name} and {duplicate}",
                )


NEW_LINES_AFTER_CLS = 2


def make_canonical(name: str, *, is_type: bool = False) -> str:
    _ = is_type
    """Replace anything but 'a-z', 'A-Z' and '0-9' with '_'."""

    return re.sub(r"[^a-zA-Z0-9]", "_", name)


def camel_to_snake_case(name: str, *, is_type: bool = False) -> str:
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub(r"(_+)", "_", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)

    if is_type:
        words = name.split("_")
        return "".join(word.capitalize() for word in words if word)

    return make_canonical(name.lower())


def format_code(code: str) -> str:
    """Format code using ruff. Returns unformatted code with warning if ruff unavailable."""
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "ruff", "format", "-"],
            input=code,
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        code = result.stdout
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "ruff", "check", "--select", "I,TCH,COM", "--fix", "-"],
            check=False,
            input=code,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return result.stdout  # noqa: TRY300
    except subprocess.CalledProcessError as e:
        if "No module named ruff" in e.stderr:
            warnings.warn(
                "ruff is not available, code will not be formatted",
                RuntimeWarning,
                stacklevel=2,
            )
        else:
            warnings.warn(f"ruff formatting failed: {e.stderr}", RuntimeWarning, stacklevel=2)
        return code
    except subprocess.TimeoutExpired:
        warnings.warn(
            "ruff formatting timed out, code will not be formatted",
            RuntimeWarning,
            stacklevel=2,
        )
        return code


def _generate_message_code(
    message: Message,
    config: CodegenOptions,
    db_var_name: str = "DB",
) -> Iterator[str]:
    yield "@dataclass"
    yield f"class {config.convert_name(message.name, is_type=True)}:"
    yield (
        f"{config.indent}struct: ClassVar[Message] = "
        f'{db_var_name}.get_message_by_name("{message.name}")'
    )
    yield from (config.indent + s for s in _generate_signal_fields(message.signals, config))


def _find_signal_type(signal: Signal) -> type[CanBasicTypes]:
    if signal.choices:
        return str
    if signal.conversion.scale != 1.0:
        return float
    if signal.offset.is_integer():
        return int
    return float


def _generate_signal_fields(signals: Iterable[Signal], config: CodegenOptions) -> Iterator[str]:
    for sig in signals:
        sig_type = _find_signal_type(sig)
        sig_type_annotation = (
            f'Annotated[{SignalContainer.__name__}[{sig_type.__name__}], "{sig.name}"]'
        )
        yield (
            f"{config.convert_name(sig.name)}: {sig_type_annotation}"
            f" =  field(default_factory="
            f'SignalContainer.get_factory(struct.get_signal_by_name("{sig.name}")))'
        )


def generate_dataclasses(
    messages: Iterable[Message],
    config: CodegenOptions | None = None,
    db_var_name: str = "DB",
) -> dict[str, list[str]]:
    config = config or CodegenOptions()
    datacls_def: dict[str, list[str]] = {}
    for message in messages:
        datacls_def[message.name] = list(
            _generate_message_code(message, config, db_var_name=db_var_name),
        )
    return datacls_def


def _generate_node(
    node_name: str,
    message_cls_names: Iterable[str],
    config: CodegenOptions,
) -> Iterator[str]:
    yield "@dataclass"
    yield f"class {node_name}:"
    for msg_name in message_cls_names:
        cls_name = config.convert_name(msg_name, is_type=True)
        field_name = config.convert_name(msg_name)
        cls_type_annotation = f'Annotated[{cls_name}, "{msg_name}"]'
        yield (
            f"{config.indent}{field_name}: {cls_type_annotation} = field(default_factory={cls_name})"
        )


DEFAULT_NODE_NAME = "Node"


def build_module(
    database: Database,
    database_path: Path | None = None,
    config: CodegenOptions | None = None,
    node_name: str | None = None,
) -> str:
    node_name = node_name or DEFAULT_NODE_NAME
    config = config or CodegenOptions()
    sanity_checks(database, config)
    # imports
    lines = [
        "from __future__ import annotations\n",
        "from dataclasses import dataclass, field",
        "from typing import Annotated, ClassVar\n",
        "import cantools.database\n",
        "from cantools.database.can import Database",
        "from canviewer.script import SignalContainer",
        "from cantools.database import Message  # noqa: TC002",
    ]
    lines.extend(config.add_gap_after_cls())
    # loading database

    db_var_name = "DB" if database_path is None else config.convert_name(database_path.stem).upper()
    if config.inline_database:
        db_content_var_name = f"{db_var_name}_CONTENT"
        lines.extend(
            [
                f'{db_content_var_name}="""\\',
                *database.as_kcd_string().split("\n"),
                '"""',
                f"{db_var_name}=cantools.database.load_string({db_content_var_name})",
            ],
        )
    else:
        if database_path is None:
            raise ValueError(
                "Database must be inlined in the generated code when database path is omitted. "
                "Use inline_database=True in config or give the database path",
            )
        lines.extend([f'{db_var_name}=cantools.database.load_file("{database_path}")'])
    # note: this is required to narrow type down properly
    # as the `load_database` is generic and can return other types than CAN Databases
    lines.append(f"assert isinstance({db_var_name}, Database)")
    msg_cls_map = generate_dataclasses(database.messages, db_var_name=db_var_name)
    for msg_dataclass_def in msg_cls_map.values():
        lines.extend(msg_dataclass_def)
        lines.extend(config.add_gap_after_cls())

    lines.extend(_generate_node(node_name, msg_cls_map.keys(), config))
    lines.append("")
    if config.generate_main:
        lines.extend(_generate_main(config, node_name))
    return "\n".join(lines)


def _generate_main(config: CodegenOptions, node_cls_name: str) -> Iterator[str]:
    yield 'if __name__ == "__main__":'
    yield from (
        config.indent + s
        for s in [
            "from pprint import pprint",
            f"pprint({node_cls_name}())",
        ]
    )


def transpile_database(
    db_path: Path,
    output_path: Path | None = None,
    config: CodegenOptions | None = None,
    node_name: str | None = None,
) -> Path:
    config = config or CodegenOptions()
    if output_path is None:
        output_path = db_path.parent / (config.convert_name(db_path.stem) + ".py")
    db = cantools.database.load_file(db_path)
    assert isinstance(db, Database)
    node_name = node_name or config.convert_name(db_path.stem, is_type=True)
    module_def = build_module(db, db_path, node_name=node_name, config=config)
    if config.format_code:
        module_def = format_code(module_def)
    output_path.write_text(module_def)
    return output_path
