import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, calculation, command, config, discovery, submission
from .model import Allocation, MemoryMode, Request, SlurmAutoError, unsigned_count


class StoreOnce(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            raise argparse.ArgumentError(self, f"{option_string} may be used only once")
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)


def positive_count(text: str) -> int:
    try:
        count = unsigned_count(text[1:] if text.startswith("+") else text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not count:
        raise argparse.ArgumentTypeError("value must be positive")
    return count


def add_resource_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-p", "--partition", action=StoreOnce, help="Partition name")
    parser.add_argument("-N", "--nodes", action=StoreOnce, type=positive_count, help="Node count (default: 1)")
    counts = parser.add_mutually_exclusive_group()
    counts.add_argument(
        "-g", "--gpus-per-node", action=StoreOnce, type=positive_count, help="GPUs per node for GPU partitions"
    )
    counts.add_argument(
        "-c", "--cpus-per-node", action=StoreOnce, type=positive_count,
        help="CPUs per node for CPU partitions (one CPU per task)",
    )
    parser.add_argument(
        "--memory", choices=[mode.value for mode in MemoryMode], action=StoreOnce,
        help="Memory request policy (default: omit, overrides configuration)",
    )
    parser.add_argument("--config", action=StoreOnce, type=Path, help="Configuration file path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbatch-auto", allow_abbrev=False,
        description="Inspect Slurm capacity, plan allocations, and submit jobs",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser(
        "inspect", allow_abbrev=False, help="Show homogeneous configured partition capacities, including busy nodes",
    )
    plan = actions.add_parser(
        "plan", allow_abbrev=False, help="Calculate a resource allocation and print an sbatch preview",
    )
    submit = actions.add_parser(
        "submit", allow_abbrev=False, help="Submit calculated resources with configured sbatch defaults",
        epilog="To use a batch script, append -- SCRIPT [ARG ...] to the command.",
    )
    help_parser = actions.add_parser("help", allow_abbrev=False, help="Print help for a command")
    help_parser.add_argument("topic", nargs="?", choices=("inspect", "plan", "submit", "help"))
    help_parser.set_defaults(topics={"inspect": inspect, "plan": plan, "submit": submit, "help": help_parser})
    add_resource_options(plan)
    add_resource_options(submit)
    submit.add_argument("-A", "--account", action=StoreOnce, help="Slurm account")
    submit.add_argument("-t", "--time", action=StoreOnce, help="Time limit")
    submit.add_argument("-J", "--job-name", action=StoreOnce, help="Job name")
    submit.add_argument("--wrap", action=StoreOnce, help="Command to wrap as a batch job")
    submit.add_argument(
        "--dry-run", action=StoreOnce, nargs=0, const=True, help="Print the command without executing sbatch"
    )
    return parser


def inspect_cluster() -> None:
    partitions = discovery.discover()
    row = "{:<20} {:<4} {:>8} {:>12} {:>12} {:>18}"
    print(row.format("PARTITION", "TYPE", "NODES", "CPUs/NODE", "GPUs/NODE", "MEMORY/NODE (MiB)"))
    for partition in partitions.values():
        capacity = partition.capacity
        print(row.format(
            partition.name, capacity.kind, len(partition.nodes),
            capacity.cpus, capacity.gpus, capacity.memory_mib,
        ))
    if not partitions:
        print("No configured partitions found in node membership.")


def print_allocation(allocation: Allocation) -> None:
    print(f"Partition: {allocation.partition}")
    print(f"Nodes: {allocation.nodes}")
    print(f"Tasks per node (one CPU per task): {allocation.tasks_per_node}")
    if allocation.memory_mib_per_node is None:
        print("Memory per node: unspecified (Slurm default)")
    else:
        print(f"Memory per node: {allocation.memory_mib_per_node} MiB")
    if allocation.gpus_per_node is not None:
        print(f"GPUs per node: {allocation.gpus_per_node}")


def make_request(args: argparse.Namespace, defaults: config.Defaults) -> Request:
    return Request(
        partition=config.select_partition(args.partition, defaults),
        nodes=args.nodes if args.nodes is not None else 1,
        gpus_per_node=args.gpus_per_node,
        cpus_per_node=args.cpus_per_node,
        memory=MemoryMode(args.memory) if args.memory is not None else defaults.memory or MemoryMode.OMIT,
    )


def run(args: argparse.Namespace, script: Sequence[str]) -> None:
    if args.action == "inspect":
        inspect_cluster()
    elif args.action == "plan":
        defaults = config.load_for_plan(args.config, args.partition)
        allocation = calculation.calculate(make_request(args, defaults), discovery.discover())
        print_allocation(allocation)
        print("\nPreview (resource arguments only; no job submitted):")
        print(command.render(command.sbatch_args(allocation)))
    else:
        defaults = config.load_config(args.config if args.config is not None else config.default_path())
        request = make_request(args, defaults)
        overrides = config.Defaults(account=args.account, time=args.time, job_name=args.job_name, wrap=args.wrap)
        options = submission.resolve(defaults, overrides, bool(script))
        allocation = calculation.calculate(request, discovery.discover())
        sbatch_args = submission.merge(allocation, options, script)
        print_allocation(allocation)
        if args.dry_run:
            print("\nDry run (no job submitted):")
            print(command.render(sbatch_args))
        else:
            submission.execute(sbatch_args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    script = []
    if arguments[:1] == ["submit"] and "--" in arguments:
        marker = arguments.index("--")
        arguments, script = arguments[:marker], arguments[marker + 1 :]
    args = parser.parse_args(arguments)
    if args.action == "help":
        args.topics.get(args.topic, parser).print_help()
        return 0
    try:
        run(args, script)
    except SlurmAutoError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    return 0
