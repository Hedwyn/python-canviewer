"""
Code generation: builds a python model out of a CAN Database.

@author: Baptiste Pestourie
@date: 24.06.2026
"""

from __future__ import annotations

import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Literal,
    Protocol,
    assert_never,
)

import cantools.database
from cantools.database.can import Database

from ._core import CanInterface, MessageMixin, SignalContainer

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from cantools.database import Message
    from cantools.database.can.signal import Signal

    from canviewer._jsonify import CanBasicTypes

type BuiltinNameConversions = Literal["camel_to_snake", "canonical"]

NEW_LINES_AFTER_CLS = 2
DEFAULT_NODE_NAME = "Node"


class NameConversionFn(Protocol):
    def __call__(self, name: str, *, is_type: bool = False) -> str: ...


@dataclass
class CodegenOptions:
    flatten_signals_tree: bool = False
    prefix_alias_names_with_msg: bool = False
    enforce_snakecase: bool = False
    name_conversion: NameConversionFn | BuiltinNameConversions = "camel_to_snake"
    inline_database: bool = False
    add_top_level_signal_aliases: bool = False
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
    if not config.flatten_signals_tree or config.prefix_alias_names_with_msg:
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
            "SignalContainer.get_factory("
            f'struct.get_signal_by_name("{sig.name}"), {sig_type.__name__}))'
        )


def _generate_message_code(
    message: Message,
    config: CodegenOptions,
    db_var_name: str = "DB",
) -> Iterator[str]:
    yield "@dataclass"
    yield f"class {config.convert_name(message.name, is_type=True)}({MessageMixin.__name__}):"
    yield (
        f"{config.indent}struct: ClassVar[Message] = "
        f'{db_var_name}.get_message_by_name("{message.name}")'
    )
    yield from (config.indent + s for s in _generate_signal_fields(message.signals, config))


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
    database: Database,
    config: CodegenOptions,
) -> Iterator[str]:
    yield "@dataclass"
    yield f"class {node_name}({CanInterface.__name__}):"
    db_name = config.convert_name(node_name).upper()
    yield f"{config.indent}database = {db_name}"
    for msg in database.messages:
        msg_name = msg.name
        cls_name = config.convert_name(msg_name, is_type=True)
        field_name = config.convert_name(msg_name)
        cls_type_annotation = f'Annotated[{cls_name}, "{msg_name}"]'
        yield (
            f"{config.indent}{field_name}: {cls_type_annotation} = field(default_factory={cls_name})"
        )
    if not config.add_top_level_signal_aliases:
        return
    # note: relying on sanity checks for detecting name collision
    for msg in database.messages:
        for signal in msg.signals:
            sig_type = _find_signal_type(signal)
            signal_name = config.convert_name(signal.name)
            msg_name = config.convert_name(msg.name)
            prop_name = (
                f"{msg_name}_{signal_name}" if config.prefix_alias_names_with_msg else signal_name
            )
            yield f"{config.indent}@cached_property"
            yield (
                f"{config.indent}def {prop_name}(self) -> "
                f"{SignalContainer.__name__}[{sig_type.__name__}]:"
            )
            yield f"{2 * config.indent}return self.{msg_name}.{signal_name}"


def _generate_main(config: CodegenOptions, node_cls_name: str) -> Iterator[str]:
    yield 'if __name__ == "__main__":'
    yield from (
        config.indent + s
        for s in [
            "from pprint import pprint",
            f"pprint({node_cls_name}())",
        ]
    )


def build_module(
    database: Database,
    database_path: Path | None = None,
    config: CodegenOptions | None = None,
    node_name: str | None = None,
) -> str:
    node_name = node_name or DEFAULT_NODE_NAME
    config = config or CodegenOptions()
    sanity_checks(database, config)
    lines = [
        "from __future__ import annotations\n",
        "from dataclasses import dataclass, field",
        "from typing import Annotated, ClassVar\n",
        "import cantools.database\n",
        "from cantools.database.can import Database",
        "from canviewer.script import SignalContainer, MessageMixin, CanInterface",
        "from cantools.database import Message  # noqa: TC002",
    ]
    lines.extend(config.add_gap_after_cls())
    if config.add_top_level_signal_aliases:
        lines.append("from functools import cached_property")

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
    lines.append(f"assert isinstance({db_var_name}, Database)")
    msg_cls_map = generate_dataclasses(database.messages, db_var_name=db_var_name)
    for msg_dataclass_def in msg_cls_map.values():
        lines.extend(msg_dataclass_def)
        lines.extend(config.add_gap_after_cls())

    lines.extend(_generate_node(node_name, database, config))
    lines.append("")
    if config.generate_main:
        lines.extend(_generate_main(config, node_name))
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
