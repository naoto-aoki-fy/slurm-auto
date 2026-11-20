import re
import subprocess
import sys
from typing import List, Sequence, Tuple

from . import command
from .config import Defaults
from .model import Allocation, SlurmAutoError

# Long prefixes of these options belong to the wrapper too, so getopt
# abbreviations cannot bypass the resource policy.
MANAGED = (
    "partition", "nodes", "ntasks", "ntasks-per-node", "ntasks-per-core",
    "ntasks-per-socket", "ntasks-per-gpu", "gpus", "gpus-per-node", "gpus-per-task",
    "gpus-per-socket", "cpus-per-task", "cpus-per-gpu", "mem", "mem-per-cpu",
    "mem-per-gpu", "mem-per-socket", "mincpus", "sockets-per-node", "cores-per-socket",
    "threads-per-core", "extra-node-info", "hint", "overcommit", "oversubscribe",
    "exclusive", "core-spec", "thread-spec", "tres-per-task", "tres-per-node",
    "tres-per-job", "tres-per-socket",
)
MANAGED_SHORT = frozenset("pNnGgcBOsS")
OVERRIDABLE = frozenset(("account", "time", "job-name", "wrap"))
NON_SUBMITTING = ("help", "usage", "version", "test-only", "completion")

VALUE_OPTIONS = frozenset((
    "account", "acctg-freq", "array", "batch", "bb", "bbf", "begin", "chdir",
    "cluster-constraint", "clusters", "comment", "constraint", "container",
    "container-id", "cpu-freq", "deadline", "delay-boot", "dependency", "distribution",
    "error", "exclude", "export", "export-file", "get-user-env", "gid", "gpu-bind",
    "gpu-freq", "gres", "gres-flags", "input", "job-name", "licenses", "mail-type",
    "mail-user", "mcs-label", "network", "nice", "nodelist", "open-mode", "output",
    "power", "priority", "profile", "propagate", "qos", "reservation", "signal",
    "switches", "time", "time-min", "uid", "wait-all-nodes", "wckey", "wrap",
))
FLAGS = frozenset((
    "contiguous", "hold", "ignore-pbs", "no-kill", "no-requeue", "parsable", "quiet",
    "reboot", "requeue", "use-min-nodes", "verbose", "wait", "spread-job",
))
SHORT_NAMES = {
    "A": "account", "a": "array", "b": "begin", "C": "constraint",
    "D": "chdir", "d": "dependency", "e": "error", "i": "input", "J": "job-name",
    "L": "licenses", "M": "clusters", "m": "distribution", "o": "output", "q": "qos",
    "t": "time", "w": "nodelist", "x": "exclude", "H": "hold", "Q": "quiet",
    "v": "verbose", "W": "wait", "k": "no-kill", "r": "no-requeue",
}


def argument_error(argument: str, reason: str) -> SlurmAutoError:
    return SlurmAutoError(f"invalid sbatch argument {argument!r}: {reason}")


def managed_error(argument: str) -> SlurmAutoError:
    return argument_error(
        argument,
        "conflicts with automatically managed resources; remove it and use the submit resource options",
    )


def parse_arguments(args: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """Validate option-only argv and retain the original tokens for each option."""
    options = []
    index = 0
    wrap = False
    while index < len(args):
        start = index
        argument = args[index]
        if "\0" in argument:
            raise argument_error(argument, "NUL bytes cannot be passed to sbatch")

        if argument.startswith("--"):
            name, separator, attached = argument[2:].partition("=")
            if not re.fullmatch(r"[A-Za-z0-9-]+", name):
                raise argument_error(argument, "expected a complete sbatch option name")
            if any(owned.startswith(name) for owned in MANAGED):
                raise managed_error(argument)
            if any(full != name and full.startswith(name) for full in (*OVERRIDABLE, "gres")):
                raise argument_error(
                    argument,
                    "use the complete --account, --time, --job-name, --gres, or --wrap option name",
                )
            if any(option.startswith(name) for option in NON_SUBMITTING):
                raise argument_error(argument, "does not submit a single job")
            if name in FLAGS and separator:
                raise argument_error(argument, "this flag does not take a value")
        elif argument.startswith("-"):
            short = argument[1:2]
            if short in MANAGED_SHORT:
                raise managed_error(argument)
            if short not in SHORT_NAMES:
                raise argument_error(
                    argument,
                    "unsupported short option; use its full long spelling (short option clusters are unsupported)",
                )
            name = SHORT_NAMES[short]
            attached = argument[2:]
            separator = bool(attached)
            if name in FLAGS and separator:
                raise argument_error(argument, "short option clusters are unsupported; separate each option")
        else:
            raise argument_error(
                argument, "sbatch_args contains options only; pass a batch script after -- on the submit command"
            )

        if name not in FLAGS:
            next_value = args[index + 1] if index + 1 < len(args) else None
            if separator:
                value = attached
            elif name not in VALUE_OPTIONS and (next_value is None or next_value.startswith("-")):
                # Unknown site options without a following value are flags.
                value = None
            else:
                if next_value is None:
                    raise argument_error(argument, "missing option value")
                if next_value.startswith("-"):
                    raise argument_error(
                        argument, "missing value; use --option=value for a value beginning with '-'"
                    )
                value = next_value
                index += 1
            if value is not None:
                if not value or "\0" in value:
                    raise argument_error(argument, "option value must be nonempty and contain no NUL bytes")
                if name == "gres" and any(
                    entry.split(":", 1)[0] in ("gpu", "mps", "shard") for entry in value.split(",")
                ):
                    raise managed_error(argument)

        if name == "wrap":
            if wrap:
                raise argument_error(argument, "--wrap must appear only once")
            wrap = True
        options.append((name, list(args[start : index + 1])))
        index += 1
    return options


def resolve(defaults: Defaults, overrides: Defaults, script_supplied: bool) -> List[str]:
    defaults.validate()
    overrides.validate()
    merged: List[Tuple[str, List[str]]] = []
    for layer in (defaults.arguments(script_supplied), defaults.sbatch_args, overrides.arguments(False)):
        # Validate each layer before merging so overrides cannot hide invalid options.
        for name, args in parse_arguments(layer):
            if name in OVERRIDABLE:
                merged = [(existing, tokens) for existing, tokens in merged if existing != name]
            merged.append((name, args))
    return [arg for _, args in merged for arg in args]


def merge(allocation: Allocation, options: Sequence[str], script: Sequence[str]) -> List[str]:
    wrap = any(name == "wrap" for name, _ in parse_arguments(options))
    if wrap == bool(script):
        raise SlurmAutoError(
            "provide exactly one workload: --wrap (CLI, sbatch_args, or default) "
            "OR a batch script after --"
        )
    if script and (not script[0] or any("\0" in arg for arg in script)):
        raise SlurmAutoError("batch script path must be nonempty; script arguments cannot contain NUL bytes")
    args = command.sbatch_args(allocation)
    args.extend(options)
    if script:
        args.extend(["--", *script])
    return args


def execute(args: Sequence[str]) -> None:
    try:
        output = subprocess.run(["sbatch", *args], stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as error:
        raise SlurmAutoError(f"failed to execute sbatch: {error}") from error
    try:
        sys.stdout.flush()
        sys.stdout.buffer.write(output.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(output.stderr)
        sys.stderr.buffer.flush()
    except OSError as error:
        raise SlurmAutoError(f"failed to forward sbatch output: {error}") from error
    if output.returncode:
        status = (
            f"exit status: {output.returncode}"
            if output.returncode > 0
            else f"signal: {-output.returncode}"
        )
        raise SlurmAutoError(
            f"sbatch failed ({status}); see its output above", max(1, output.returncode)
        )

    # Standard sbatch output already contains a job ID. Label --parsable output too.
    job_id = output.stdout.decode("utf-8", errors="replace").strip().split(";", 1)[0]
    if job_id.isascii() and job_id.isdigit():
        print(f"Submitted job ID: {job_id}")
