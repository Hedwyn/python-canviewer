"""
Hatch build hook for canviewer codegen utilities.

This plugin allows code generation from CAN database files during the build process.
Users can configure it in their pyproject.toml to transpile CAN databases to Python.

Configuration in pyproject.toml:

[tool.hatch.build.hooks.canviewer-codegen]
database = "path/to/database.kcd"
output = "path/to/output.py"  # optional, defaults to next to the database
node-name = "MyNode"  # optional

[tool.hatch.build.hooks.canviewer-codegen]
flatten-signals-tree = true
prefix-alias-names-with-msg = false
name-conversion = "camel_to_snake"
format-code = false
generate-main = false
indent = "    "
new-lines-after-cls = 2

For multiple databases:

[tool.hatch.build.hooks.canviewer-codegen]
targets = [
    { database = "path/to/database1.kcd", output = "output1.py" },
    { database = "path/to/database2.kcd", node-name = "Node2" },
]

@date: 26.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.plugin import hookimpl

from ._codegen import CodegenOptions, transpile_database


class TargetConfigDict(TypedDict, total=False):
    database: str
    output: str
    node_name: str
    flatten_signals_tree: bool
    prefix_alias_names_with_msg: bool
    enforce_snakecase: bool
    name_conversion: str
    inline_database: bool
    add_top_level_signal_aliases: bool
    format_code: bool
    generate_main: bool
    indent: str
    new_lines_after_cls: int


class CanviewerCodegenBuildHook(BuildHookInterface):
    PLUGIN_NAME = "canviewer-codegen"

    def initialize(self, version: str, build_data: object) -> None:
        targets: object = self.config.get("targets")
        if targets is None:
            self._validate_config(self.config)
            self._process_target(self.config)
        else:
            assert isinstance(targets, list), "targets must be a list"
            for target in targets:
                assert isinstance(target, dict), "each target must be a dictionary"
                self._validate_config(target)
                self._process_target(target)

    def _validate_config(self, config: object) -> None:
        assert isinstance(config, dict), "config must be a dictionary"

        db_path_str = config.get("database")
        if db_path_str is not None:
            assert isinstance(db_path_str, str), "database must be a string"

        output_path_str = config.get("output")
        if output_path_str is not None:
            assert isinstance(output_path_str, str), "output must be a string"

        node_name = config.get("node-name")
        if node_name is not None:
            assert isinstance(node_name, str), "node-name must be a string"

        for bool_key in [
            "flatten-signals-tree",
            "prefix-alias-names-with-msg",
            "enforce-snakecase",
            "inline-database",
            "add-top-level-signal-aliases",
            "format-code",
            "generate-main",
        ]:
            if bool_key in config:
                value = config[bool_key]
                assert isinstance(value, bool), (
                    f"{bool_key} must be a boolean, got {type(value).__name__}"
                )

        for str_key in ["name-conversion", "indent"]:
            if str_key in config:
                value = config[str_key]
                assert isinstance(value, str), (
                    f"{str_key} must be a string, got {type(value).__name__}"
                )

        if "new-lines-after-cls" in config:
            value = config["new-lines-after-cls"]
            assert isinstance(value, int) and not isinstance(value, bool), (  # noqa: PT018
                "new-lines-after-cls must be an integer"
            )
            assert value >= 0, "new-lines-after-cls must be non-negative"

    def _process_target(self, target: object) -> None:
        assert isinstance(target, dict), "target must be a dictionary"

        db_path_str = target.get("database")
        if not db_path_str:
            return

        assert isinstance(db_path_str, str)
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = Path(self.root) / db_path

        output_path_str = target.get("output")
        output_path: Path | None = None
        if output_path_str:
            assert isinstance(output_path_str, str)
            output_path = Path(output_path_str)
            if not output_path.is_absolute():
                output_path = Path(self.root) / output_path

        codegen_options = self._build_codegen_options(target)

        node_name = target.get("node-name")

        transpile_database(
            db_path=db_path,
            output_path=output_path,
            config=codegen_options,
            node_name=node_name,
        )

    def _build_codegen_options(self, config_dict: object) -> CodegenOptions:
        assert isinstance(config_dict, dict)
        return CodegenOptions(
            flatten_signals_tree=self._get_bool(config_dict, "flatten-signals-tree", False),  # noqa: FBT003
            prefix_alias_names_with_msg=self._get_bool(
                config_dict,
                "prefix-alias-names-with-msg",
                False,  # noqa: FBT003
            ),
            enforce_snakecase=self._get_bool(config_dict, "enforce-snakecase", False),  # noqa: FBT003
            name_conversion=self._get_string(config_dict, "name-conversion", "camel_to_snake"),
            inline_database=self._get_bool(config_dict, "inline-database", False),  # noqa: FBT003
            add_top_level_signal_aliases=self._get_bool(
                config_dict,
                "add-top-level-signal-aliases",
                False,  # noqa: FBT003
            ),
            indent=self._get_string(config_dict, "indent", "    "),
            new_lines_after_cls=self._get_int(config_dict, "new-lines-after-cls", 2),
            format_code=self._get_bool(config_dict, "format-code", False),  # noqa: FBT003
            generate_main=self._get_bool(config_dict, "generate-main", False),  # noqa: FBT003
        )

    @staticmethod
    def _get_bool(config_dict: object, key: str, default: bool) -> bool:  # noqa: FBT001
        assert isinstance(config_dict, dict)
        value = config_dict.get(key, default)
        if isinstance(value, bool):
            return value
        return default

    @staticmethod
    def _get_string(config_dict: object, key: str, default: str) -> str:
        assert isinstance(config_dict, dict)
        value = config_dict.get(key, default)
        if isinstance(value, str):
            return value
        return default

    @staticmethod
    def _get_int(config_dict: object, key: str, default: int) -> int:
        assert isinstance(config_dict, dict)
        value = config_dict.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return default


@hookimpl
def hatch_register_build_hook() -> type[CanviewerCodegenBuildHook]:
    """
    Registers the CAN codegen hook in the hatch build pipeline.
    """
    return CanviewerCodegenBuildHook
