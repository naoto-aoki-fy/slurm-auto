import argparse
import sys
from typing import Iterable

from .aggregation import aggregate_partitions
from .errors import DiscoveryError
from .models import PartitionResources
from .parsing import parse_nodes
from .slurm import retrieve_node_json


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Slurm partition resources")
    parser.add_argument("--list-partitions", action="store_true",
                        help="discover nodes and display partition resources")
    args = parser.parse_args(argv)
    if not args.list_partitions:
        parser.print_help()
        return 0
    try:
        partitions = aggregate_partitions(parse_nodes(retrieve_node_json()))
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_partitions(partitions))
    return 0
