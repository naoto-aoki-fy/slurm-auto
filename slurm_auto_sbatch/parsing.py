"""Pure parsing of Slurm JSON and resource fields."""

import json
import re
from typing import List, Tuple

from .errors import DiscoveryError
from .models import NodeResources


def _gres_entries(value: str) -> List[str]:
    # Socket annotations may themselves contain commas, e.g. (S:0,1).
    entries = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth < 0:
            raise DiscoveryError(f"Malformed GRES annotation: {value!r}")
        if char == "," and depth == 0:
            entries.append(value[start:index].strip())
            start = index + 1
    if depth:
        raise DiscoveryError(f"Unclosed GRES annotation: {value!r}")
    entries.append(value[start:].strip())
    if any(not entry for entry in entries):
        raise DiscoveryError(f"Empty entry in GRES: {value!r}")
    return entries


def parse_gpu_count(gres: object) -> int:
    if gres is None:
        return 0
    if not isinstance(gres, str):
        raise DiscoveryError("gres must be a string or null")
    gres = gres.strip()
    if gres in ("", "(null)", "null"):
        return 0
    total = 0
    for entry in _gres_entries(gres):
        resource = entry.split("(", 1)[0]
        if resource.split(":", 1)[0] != "gpu":
            continue
        match = re.fullmatch(r"gpu:(?:[^:,()\s]+:)?([0-9]+)(?:\([^()]*\))?", entry)
        if match is None:
            raise DiscoveryError(f"Malformed GPU GRES entry: {entry!r}")
        total += int(match.group(1))
    return total


def _partitions(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if value.strip() in ("", "(null)", "null"):
            return ()
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        raise DiscoveryError("partitions must be a string, a list of names, or null")
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise DiscoveryError("partitions contains an empty or non-string name")
    return tuple(dict.fromkeys(part.strip() for part in parts))


def parse_nodes(payload: str) -> Tuple[NodeResources, ...]:
    try:
        document = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise DiscoveryError(f"Invalid Slurm node JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise DiscoveryError("Slurm node JSON must be an object")
    if document.get("errors"):
        raise DiscoveryError(f"Slurm reported errors: {document['errors']!r}")
    if not isinstance(document.get("nodes"), list):
        raise DiscoveryError("Slurm node JSON requires a 'nodes' array")
    nodes = []
    seen = set()
    for index, raw in enumerate(document["nodes"]):
        context = f"nodes[{index}]"
        try:
            if not isinstance(raw, dict):
                raise DiscoveryError("node must be an object")
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                raise DiscoveryError("name must be a nonempty string")
            context += f" ({name})"
            if name in seen:
                raise DiscoveryError("duplicate node name")
            cpus = raw.get("effective_cpus")
            field = "effective_cpus"
            if cpus is None:
                cpus = raw.get("cpus")
                field = "cpus"
            if isinstance(cpus, bool) or not isinstance(cpus, int) or cpus <= 0:
                raise DiscoveryError(f"{field} must be a positive integer")
            for required in ("partitions", "gres"):
                if required not in raw:
                    raise DiscoveryError(f"missing required field {required!r}")
            nodes.append(NodeResources(name, _partitions(raw["partitions"]), cpus,
                                       parse_gpu_count(raw["gres"])))
            seen.add(name)
        except DiscoveryError as exc:
            raise DiscoveryError(f"{context}: {exc}") from exc
    return tuple(nodes)
