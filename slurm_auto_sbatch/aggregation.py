from typing import Iterable, Tuple

from .models import NodeResources, PartitionResources
from .validation import validate_resources


def aggregate_partitions(nodes: Iterable[NodeResources]) -> Tuple[PartitionResources, ...]:
    members = {}
    for node in nodes:
        for partition in node.partitions:
            members.setdefault(partition, []).append(node)
    partitions = []
    for name, group in sorted(members.items()):
        group = tuple(sorted(group, key=lambda node: node.name))
        cpus = {node.cpus for node in group}
        gpus = {node.gpus for node in group}
        partitions.append(PartitionResources(
            name=name,
            nodes=group,
            cpus_per_node=next(iter(cpus)) if len(cpus) == 1 else None,
            gpus_per_node=next(iter(gpus)) if len(gpus) == 1 else None,
            errors=validate_resources(group),
        ))
    return tuple(partitions)
