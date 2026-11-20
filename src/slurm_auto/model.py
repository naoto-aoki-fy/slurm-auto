import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

U64_MAX = (1 << 64) - 1
# Unicode White_Space used by Rust's trim and is_whitespace. Python also
# treats the four ASCII information separators U+001C through U+001F as space.
WHITESPACE = (
    " \t\n\r\v\f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


class SlurmAutoError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class MemoryMode(str, Enum):
    OMIT = "omit"
    PROPORTIONAL = "proportional"


@dataclass(frozen=True)
class Capacity:
    cpus: int
    gpus: int
    memory_mib: int

    @property
    def kind(self) -> str:
        return "GPU" if self.gpus else "CPU"


@dataclass(frozen=True)
class Node:
    name: str
    partitions: Tuple[str, ...]
    capacity: Capacity


@dataclass(frozen=True)
class Partition:
    name: str
    nodes: Tuple[str, ...]
    capacity: Capacity


@dataclass(frozen=True)
class Request:
    partition: str
    nodes: int = 1
    gpus_per_node: Optional[int] = None
    cpus_per_node: Optional[int] = None
    memory: MemoryMode = MemoryMode.OMIT


@dataclass(frozen=True)
class Allocation:
    partition: str
    nodes: int
    gpus_per_node: Optional[int]
    tasks_per_node: int
    memory_mib_per_node: Optional[int]


def unsigned_count(text: str) -> int:
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("expected an unsigned integer")
    digits = text.lstrip("0") or "0"
    if len(digits) > 20 or int(digits) > U64_MAX:
        raise ValueError("value exceeds the unsigned 64-bit range")
    return int(digits)
