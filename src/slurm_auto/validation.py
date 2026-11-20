from typing import Dict, Iterable, List, Optional

from .model import Node, Partition, Request, SlurmAutoError


def aggregate(nodes: Iterable[Node]) -> Dict[str, Partition]:
    grouped: Dict[str, List[Node]] = {}
    names = set()
    for node in nodes:
        if node.name in names:
            raise SlurmAutoError(f"duplicate node name {node.name!r}")
        names.add(node.name)
        if node.capacity.cpus == 0 or node.capacity.memory_mib == 0:
            raise SlurmAutoError(
                f"node {node.name!r} must have positive effective CPUs and real_memory"
            )
        for name in node.partitions:
            grouped.setdefault(name, []).append(node)

    partitions = {}
    for name, members in sorted(grouped.items()):
        capacity = members[0].capacity
        if any(node.capacity != capacity for node in members):
            details = "; ".join(
                f"{node.name}: CPUs={node.capacity.cpus}, GPUs={node.capacity.gpus}, "
                f"memory={node.capacity.memory_mib} MiB"
                for node in members
            )
            raise SlurmAutoError(f"heterogeneous partition {name!r} is unsupported: {details}")
        partitions[name] = Partition(name, tuple(node.name for node in members), capacity)
    return partitions


def validate_request(request: Request, partition: Partition) -> None:
    if request.nodes <= 0:
        raise SlurmAutoError("requested node count must be positive")
    if request.nodes > len(partition.nodes):
        raise SlurmAutoError(
            f"partition {partition.name!r} has {len(partition.nodes)} configured nodes; "
            f"requested {request.nodes}"
        )
    capacity = partition.capacity
    if capacity.cpus == 0 or capacity.memory_mib == 0:
        raise SlurmAutoError(f"partition {partition.name!r} has zero CPU or memory capacity")
    if capacity.gpus == 0:
        if request.gpus_per_node is not None:
            raise SlurmAutoError(f"--gpus-per-node is invalid for CPU partition {partition.name!r}")
        validate_count("CPUs per node", request.cpus_per_node, capacity.cpus)
    else:
        if request.cpus_per_node is not None:
            raise SlurmAutoError(
                f"--cpus-per-node is invalid for GPU partition {partition.name!r}; "
                "CPUs are proportional to GPUs"
            )
        validate_count("GPUs per node", request.gpus_per_node, capacity.gpus)


def validate_count(label: str, count: Optional[int], capacity: int) -> None:
    if count is not None and not 1 <= count <= capacity:
        raise SlurmAutoError(f"requested {label} must be between 1 and {capacity}; got {count}")
