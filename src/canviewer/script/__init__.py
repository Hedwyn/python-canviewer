"""
Scripting utilities for canviewer.

@date: 23.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
import warnings
from asyncio import Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol, Self, assert_never

import cantools.database
from cantools.database.can import Database

from canviewer import find_sound_default

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from cantools.database import Message
    from cantools.database.can.signal import Signal

    from canviewer._jsonify import CanBasicTypes

type SignalValue = int | float | str


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
    last_seen: float | None = None

    _watchers: list[Waiter[T]] = field(default_factory=list)
    _hooks: set[Callable[[Self], object]] = field(default_factory=set)

    def update(self, new_value: T, timestamp: float | None = None) -> None:
        timestamp = timestamp or time.time()
        self.last_seen = timestamp
        self.value = new_value

        for future, condition, tolerance in self._watchers:
            is_met = condition is None
            if is_met:
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
        raw_default = find_sound_default(sig)
        casted_default = sig_type(raw_default)
        yield (
            f"{config.convert_name(sig.name)}: {SignalContainer.__name__}[{sig_type.__name__}]"
            f" =  field(default_factory=lambda: SignalContainer({casted_default!r}))"
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


def _build_node(
    node_name: str,
    message_cls_names: Iterable[str],
    config: CodegenOptions,
) -> Iterator[str]:
    yield "@dataclass"
    yield f"class {node_name}:"
    for msg_name in message_cls_names:
        cls_name = config.convert_name(msg_name, is_type=True)
        field_name = config.convert_name(msg_name)
        yield f"{config.indent}{field_name}: {cls_name}"


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
        "from typing import ClassVar, TYPE_CHECKING\n",
        "import cantools.database\n",
        "from canviewer import find_sound_default",
        "from canviewer.script import SignalContainer",
        "if TYPE_CHECKING:",
        f"{config.indent}from cantools.database import Message",
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

    lines.append("")
    msg_cls_map = generate_dataclasses(database.messages, db_var_name=db_var_name)
    for msg_dataclass_def in msg_cls_map.values():
        lines.extend(msg_dataclass_def)
        lines.extend(config.add_gap_after_cls())

    lines.extend(_build_node(node_name, msg_cls_map.keys(), config))
    lines.append("")
    return "\n".join(lines)


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
