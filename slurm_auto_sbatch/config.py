"""Load argument-list templates and keep resource selection under tool control."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .errors import JobError


@dataclass(frozen=True)
class JobConfig:
    partition: Optional[str] = None
    sbatch_options: Tuple[str, ...] = ()


# Reject abbreviations too: Slurm accepts unambiguous long-option prefixes.
_RESOURCE_OPTIONS = (
    "nodes", "ntasks", "ntasks-per-node", "ntasks-per-gpu", "ntasks-per-core",
    "ntasks-per-socket", "gpus", "gpus-per-node", "gpus-per-task",
    "gpus-per-socket", "gres", "gres-flags", "cpus-per-task", "cpus-per-gpu",
    "partition", "nodefile", "nodelist", "exclude", "extra-node-info",
    "sockets-per-node", "cores-per-socket", "threads-per-core",
    "core-spec", "thread-spec", "mincpus",
)
_RESOURCE_SHORT = set("NnGcpwxBFS")
# These flags have no separate operand; optional operands must use '='.
_FLAGS = {
    "--help", "--usage", "--version", "--verbose", "--quiet", "--parsable",
    "--hold", "--no-requeue", "--requeue", "--reboot", "--test-only",
    "--wait", "--contiguous", "--overcommit", "--oversubscribe",
    "--spread-job", "--use-min-nodes", "--exclusive", "--get-user-env",
    "--ignore-pbs",
}
_SHORT_FLAGS = set("vQHOsW")
_SHORT_VALUES = set("AabCdDeiJLmMot")


def validate_template(options: Tuple[str, ...]) -> None:
    if any(not isinstance(arg, str) or "\0" in arg for arg in options):
        raise JobError("sbatch_options must contain strings without NUL characters")
    wrapped = False
    index = 0
    while index < len(options):
        token = options[index]
        if token == "--wrap" or token.startswith("--wrap="):
            if wrapped:
                raise JobError("sbatch_options must contain exactly one --wrap payload")
            if token == "--wrap":
                index += 1
                if index == len(options):
                    raise JobError("--wrap requires a command string")
                payload = options[index]
            else:
                payload = token.split("=", 1)[1]
            if not payload.strip():
                raise JobError("--wrap requires a nonempty command string")
            wrapped = True
        elif token.startswith("--"):
            name = token[2:].split("=", 1)[0]
            if not name or any(option.startswith(name) for option in _RESOURCE_OPTIONS):
                raise JobError(
                    f"conflicting template option {token!r}: resource and partition "
                    "options must be selected through the tool"
                )
            if "=" not in token and token not in _FLAGS:
                if index + 1 < len(options) and not options[index + 1].startswith("-"):
                    index += 1
        elif token.startswith("-") and token != "-":
            # Examine flag clusters, but stop once a value-taking option begins.
            for offset, letter in enumerate(token[1:], 1):
                if letter in _RESOURCE_SHORT:
                    raise JobError(f"conflicting template resource/partition option: -{letter}")
                if letter in _SHORT_VALUES:
                    if offset == len(token) - 1:
                        index += 1
                        if index == len(options) or options[index].startswith("-"):
                            raise JobError(f"template option -{letter} requires a value")
                    break
                if letter not in _SHORT_FLAGS:
                    raise JobError(f"unsupported template short option: -{letter}; use its full long form")
        else:
            raise JobError(f"unexpected template argument {token!r}; job scripts are unsupported, use --wrap")
        index += 1
    if not wrapped:
        raise JobError("sbatch_options must include a --wrap command; script files are unsupported")


def load_config(path: Optional[str]) -> JobConfig:
    if path is None:
        return JobConfig()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise JobError(f"Unable to load configuration {path!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise JobError("configuration must be a JSON object")
    unknown = set(data) - {"partition", "sbatch_options"}
    if unknown:
        raise JobError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
    partition = data.get("partition")
    if partition is not None and (not isinstance(partition, str) or not partition.strip()):
        raise JobError("configuration partition must be a nonempty string")
    options = data.get("sbatch_options", [])
    if not isinstance(options, list) or any(not isinstance(arg, str) or "\0" in arg for arg in options):
        raise JobError("sbatch_options must be a JSON array of strings without NUL characters")
    if partition is not None and "\0" in partition:
        raise JobError("configuration partition must not contain NUL characters")
    return JobConfig(partition, tuple(options))
