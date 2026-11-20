import shlex
from typing import List, Sequence

from .model import Allocation


def sbatch_args(allocation: Allocation) -> List[str]:
    args = [f"--partition={allocation.partition}", f"--nodes={allocation.nodes}"]
    if allocation.gpus_per_node is not None:
        args.append(f"--gpus-per-node={allocation.gpus_per_node}")
    args.append(f"--ntasks-per-node={allocation.tasks_per_node}")
    if allocation.memory_mib_per_node is not None:
        args.append(f"--mem={allocation.memory_mib_per_node}M")
    return args


def render(args: Sequence[str]) -> str:
    return shlex.join(["sbatch", *args])
