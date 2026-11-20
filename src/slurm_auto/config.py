import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import json_input
from .model import MemoryMode, SlurmAutoError, WHITESPACE

DEFAULT_STRING_FIELDS = ("account", "partition", "time", "wrap", "job_name")


@dataclass(frozen=True)
class Defaults:
    account: Optional[str] = None
    partition: Optional[str] = None
    time: Optional[str] = None
    wrap: Optional[str] = None
    job_name: Optional[str] = None
    memory: Optional[MemoryMode] = None
    sbatch_args: Tuple[str, ...] = ()

    def validate(self) -> None:
        for name in DEFAULT_STRING_FIELDS:
            value = getattr(self, name)
            if value is not None and (not value.strip(WHITESPACE) or "\0" in value):
                raise SlurmAutoError(
                    f"invalid configuration: sbatch.defaults.{name} must be nonempty "
                    "and contain no NUL bytes"
                )

    def arguments(self, script_supplied: bool) -> List[str]:
        fields = [
            ("account", self.account),
            ("time", self.time),
            ("job-name", self.job_name),
            ("wrap", None if script_supplied else self.wrap),
        ]
        return [f"--{name}={value}" for name, value in fields if value is not None]


def default_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg) / "slurm-auto/config.json"
    home = os.environ.get("HOME")
    if not home:
        raise SlurmAutoError("HOME is unset; provide --config <path>")
    return Path(home) / ".config/slurm-auto/config.json"


def select_partition(partition: Optional[str], defaults: Defaults) -> str:
    if partition is None:
        partition = defaults.partition
    if partition is None:
        raise SlurmAutoError("provide --partition (-p) or configure sbatch.defaults.partition")
    if not partition.strip(WHITESPACE) or "\0" in partition:
        raise SlurmAutoError("partition must be nonempty and contain no NUL bytes")
    return partition


def check_object(value: object, path: str, fields: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    for name in value:
        if name not in fields:
            raise ValueError(f"unknown field `{name}` in {path}")
    return value


def load_config(path: Path) -> Defaults:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SlurmAutoError(
            f"cannot read {path}: {error}; provide --config or install a configuration "
            "(see config.example.json)"
        ) from error

    try:
        config = check_object(json_input.load(source), "configuration", ("sbatch",))
        sbatch = check_object(config.get("sbatch", {}), "sbatch", ("defaults",))
        fields = check_object(
            sbatch.get("defaults", {}), "sbatch.defaults", (*DEFAULT_STRING_FIELDS, "memory", "sbatch_args")
        )
        for name in DEFAULT_STRING_FIELDS:
            if name in fields and not isinstance(fields[name], str):
                raise ValueError(f"sbatch.defaults.{name} must be a string")
        if "memory" in fields:
            if fields["memory"] not in ("omit", "proportional"):
                raise ValueError("sbatch.defaults.memory must be 'omit' or 'proportional'")
            fields["memory"] = MemoryMode(fields["memory"])
        arguments = fields.get("sbatch_args", [])
        if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
            raise ValueError("sbatch.defaults.sbatch_args must be an array of strings")
        fields["sbatch_args"] = tuple(arguments)
    except (ValueError, RecursionError) as error:
        raise SlurmAutoError(f"invalid configuration {path}: {error}") from error
    defaults = Defaults(**fields)
    defaults.validate()
    return defaults


def load_for_plan(path: Optional[Path], partition: Optional[str]) -> Defaults:
    if path is not None:
        return load_config(path)
    path = default_path()
    if partition is not None:
        try:
            path.stat()
        except FileNotFoundError:
            return Defaults()
        except OSError as error:
            raise SlurmAutoError(f"cannot access {path}: {error}") from error
    return load_config(path)
