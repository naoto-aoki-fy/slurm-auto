"""Calculate job resources from configured partition capacity, not live usage."""

from dataclasses import dataclass
from typing import List, Optional

from .errors import JobError
from .models import PartitionResources


@dataclass(frozen=True)
class JobResources:
    partition: str
    nodes: int
    ntasks_per_node: int
    gpus_per_node: Optional[int] = None

    def sbatch_options(self) -> List[str]:
        options = [f"--partition={self.partition}", f"--nodes={self.nodes}"]
        if self.gpus_per_node is not None:
            options.append(f"--gpus-per-node={self.gpus_per_node}")
        options.append(f"--ntasks-per-node={self.ntasks_per_node}")
        return options


def _positive(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise JobError(f"{name} must be a positive integer")


def calculate_resources(
    partition: PartitionResources, nodes: int = 1,
    gpus_per_node: Optional[int] = None, cpus_per_node: Optional[int] = None,
) -> JobResources:
    _positive(nodes, "nodes")
    if not partition.supported:
        raise JobError(f"partition {partition.name!r} is unsupported: {'; '.join(partition.errors)}")
    if nodes > partition.node_count:
        raise JobError(f"requested {nodes} nodes, but partition {partition.name!r} has {partition.node_count}")
    cpus, gpus = partition.cpus_per_node, partition.gpus_per_node
    if cpus is None or gpus is None or cpus < 1 or gpus < 0:
        raise JobError(f"partition {partition.name!r} has invalid or heterogeneous resources")
    if gpus:
        if cpus_per_node is not None:
            raise JobError("--cpus-per-node is only supported for CPU-only partitions")
        requested = gpus if gpus_per_node is None else gpus_per_node
        _positive(requested, "gpus-per-node")
        if requested > gpus:
            raise JobError(f"requested {requested} GPUs per node; partition provides {gpus}")
        tasks = max(1, cpus * requested // gpus)
        return JobResources(partition.name, nodes, tasks, requested)
    if gpus_per_node is not None:
        raise JobError("--gpus-per-node cannot be used with a CPU-only partition")
    requested = cpus if cpus_per_node is None else cpus_per_node
    _positive(requested, "cpus-per-node")
    if requested > cpus:
        raise JobError(f"requested {requested} CPUs per node; partition provides {cpus}")
    return JobResources(partition.name, nodes, requested)
