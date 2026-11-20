from typing import Mapping

from .model import Allocation, MemoryMode, Partition, Request, SlurmAutoError
from .validation import validate_request


def calculate(request: Request, partitions: Mapping[str, Partition]) -> Allocation:
    partition = partitions.get(request.partition)
    if partition is None:
        available = ", ".join(sorted(partitions))
        raise SlurmAutoError(
            f"unknown partition {request.partition!r}; available partitions: {available}"
        )
    validate_request(request, partition)
    capacity = partition.capacity
    if capacity.gpus:
        gpus = request.gpus_per_node or capacity.gpus
        tasks = capacity.cpus * gpus // capacity.gpus
        memory = capacity.memory_mib * gpus // capacity.gpus
    else:
        gpus = None
        tasks = request.cpus_per_node or capacity.cpus
        memory = capacity.memory_mib * tasks // capacity.cpus

    proportional = request.memory == MemoryMode.PROPORTIONAL
    if tasks == 0 or (proportional and memory == 0):
        raise SlurmAutoError(
            f"allocation for partition {partition.name!r} produces zero tasks or zero memory "
            f"(tasks={tasks}, memory={memory} MiB)"
        )
    return Allocation(partition.name, request.nodes, gpus, tasks, memory if proportional else None)
