from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class NodeResources:
    name: str
    partitions: Tuple[str, ...]
    cpus: int
    gpus: int

    @property
    def is_gpu(self) -> bool:
        return self.gpus > 0


@dataclass(frozen=True)
class PartitionResources:
    name: str
    nodes: Tuple[NodeResources, ...]
    cpus_per_node: Optional[int]
    gpus_per_node: Optional[int]
    errors: Tuple[str, ...]

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def has_gpu_nodes(self) -> bool:
        return any(node.is_gpu for node in self.nodes)

    @property
    def has_cpu_only_nodes(self) -> bool:
        return any(not node.is_gpu for node in self.nodes)

    @property
    def supported(self) -> bool:
        return not self.errors
