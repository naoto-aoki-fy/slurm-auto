import argparse
import shlex
import sys
from typing import Iterable

from .aggregation import aggregate_partitions
from .config import load_config, validate_template
from .errors import DiscoveryError, JobError
from .jobs import build_sbatch_command
from .models import PartitionResources
from .parsing import parse_nodes
from .resources import calculate_resources
from .slurm import retrieve_node_json, submit_job


def format_partitions(partitions: Iterable[PartitionResources]) -> str:
    lines = ["PARTITION\tNODES\tCPUS/NODE\tGPUS/NODE\tNODE TYPES\tSTATUS"]
    found = False
    for partition in partitions:
        found = True
        kinds = []
        if partition.has_gpu_nodes:
            kinds.append("GPU")
        if partition.has_cpu_only_nodes:
            kinds.append("CPU-only")
        cpus = partition.cpus_per_node
        gpus = partition.gpus_per_node
        lines.append("\t".join((
            partition.name, str(partition.node_count),
            str(cpus) if cpus is not None else "varies",
            str(gpus) if gpus is not None else "varies",
            ", ".join(kinds), "supported" if partition.supported else "UNSUPPORTED",
        )))
        lines.append(f"  Nodes: {', '.join(node.name for node in partition.nodes)}")
        for error in partition.errors:
            lines.append(f"  Unsupported: {error}")
    if not found:
        lines.append("No partitions discovered.")
    return "\n".join(lines)


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Select Slurm resources and submit a wrapped job")
    parser.add_argument("--list-partitions", action="store_true",
                        help="discover nodes and display partition resources without submitting")
    parser.add_argument("--partition", help="target partition (overrides configuration)")
    parser.add_argument("--nodes", type=positive_integer, default=1, help="nodes to request (default: 1)")
    parser.add_argument("--gpus-per-node", type=positive_integer, help="GPUs per node for GPU partitions")
    parser.add_argument("--cpus-per-node", type=positive_integer, help="CPUs per node for CPU-only partitions")
    parser.add_argument("--config", help="JSON configuration containing sbatch_options and an optional partition")
    parser.add_argument("--dry-run", action="store_true", help="display the command without calling sbatch")
    args = parser.parse_args(argv)
    try:
        if not args.list_partitions:
            config = load_config(args.config)
            partition_name = args.partition if args.partition is not None else config.partition
            if not partition_name or not partition_name.strip():
                raise JobError("select a partition with --partition or the configuration partition field")
            validate_template(config.sbatch_options)
        partitions = aggregate_partitions(parse_nodes(retrieve_node_json()))
        if args.list_partitions:
            print(format_partitions(partitions))
            return 0
        selected = next((p for p in partitions if p.name == partition_name), None)
        if selected is None:
            raise JobError(f"unknown partition {partition_name!r}; use --list-partitions to inspect configured partitions")
        resources = calculate_resources(selected, args.nodes, args.gpus_per_node, args.cpus_per_node)
        command = build_sbatch_command(resources, config)
        if args.dry_run:
            print(format_partitions((selected,)))
            print(f"Request: {resources.nodes} node(s), {resources.ntasks_per_node} CPU tasks per node "
                  f"({resources.nodes * resources.ntasks_per_node} total), "
                  f"{resources.gpus_per_node or 0} GPUs per node "
                  f"({resources.nodes * (resources.gpus_per_node or 0)} total)")
            print(shlex.join(command))
        else:
            result = submit_job(command)
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            else:
                print("sbatch completed successfully; no job ID was returned")
            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    except (DiscoveryError, JobError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
