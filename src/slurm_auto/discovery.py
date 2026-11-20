import json
import re
import subprocess
from typing import Dict, List, Tuple

from . import json_input
from .model import Capacity, Node, Partition, SlurmAutoError, U64_MAX, WHITESPACE, unsigned_count
from .validation import aggregate

GRES_ANNOTATIONS = re.compile(rf"(?:\([^()]*\)[{WHITESPACE}]*)+")


def discover() -> Dict[str, Partition]:
    invocation = ["scontrol", "show", "node", "--json"]
    try:
        output = subprocess.run(invocation, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as error:
        raise SlurmAutoError(f"failed to execute scontrol show node --json: {error}") from error
    if output.returncode:
        status = (
            f"exit status: {output.returncode}"
            if output.returncode > 0
            else f"signal: {-output.returncode}"
        )
        detail = output.stderr.decode("utf-8", errors="replace").strip(WHITESPACE)
        raise SlurmAutoError(f"scontrol show node --json failed ({status}): {detail}")
    try:
        source = output.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SlurmAutoError(f"scontrol returned non-UTF-8 JSON: {error}") from error
    return parse_cluster(source)


def parse_cluster(source: str) -> Dict[str, Partition]:
    try:
        response = json_input.load(source)
    except (ValueError, RecursionError) as error:
        raise SlurmAutoError(f"invalid scontrol node JSON: {error}") from error
    if not isinstance(response, dict) or not isinstance(response.get("nodes"), list):
        raise SlurmAutoError("invalid scontrol node JSON: expected an object with a nodes array")
    errors = response.get("errors", [])
    if not isinstance(errors, list):
        raise SlurmAutoError("invalid scontrol node JSON: expected an errors array")
    if errors:
        raise SlurmAutoError(f"scontrol reported errors: {json.dumps(errors)}")

    nodes = []
    for index, value in enumerate(response["nodes"]):
        try:
            nodes.append(parse_node(value))
        except SlurmAutoError as error:
            raise SlurmAutoError(f"node record {index}: {error}") from error
    return aggregate(nodes)


def parse_node(value: object) -> Node:
    if not isinstance(value, dict):
        raise SlurmAutoError("expected a node object")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip(WHITESPACE):
        raise SlurmAutoError("missing or malformed node name")

    try:
        if "partitions" not in value:
            raise SlurmAutoError("missing partitions field")
        partitions = parse_partitions(value["partitions"])
        effective = value.get("effective_cpus", value.get("cpus_efctv"))
        if effective is None or (isinstance(effective, dict) and effective.get("set") is False):
            cpus = number(value, "cpus")
        else:
            cpus = number({"effective_cpus": effective}, "effective_cpus")
        memory_mib = number(value, "real_memory")
        if "gres" not in value:
            raise SlurmAutoError("missing gres field; configured GPU capacity is unknown")
        gres = value["gres"]
        if gres is not None and not isinstance(gres, str):
            raise SlurmAutoError("malformed gres field: expected a string or null")
        gpus = 0 if gres is None else parse_gres(gres)
    except SlurmAutoError as error:
        raise SlurmAutoError(f"node {name!r}: {error}") from error
    return Node(name, partitions, Capacity(cpus, gpus, memory_mib))


def number(fields: Dict[str, object], field: str) -> int:
    if field not in fields:
        raise SlurmAutoError(f"missing {field} field")
    value = fields[field]
    candidate = value
    # Recent Slurm data parsers can represent numeric fields as tagged values.
    if (
        isinstance(value, dict)
        and value.get("set") is True
        and value.get("infinite", False) is False
    ):
        candidate = value.get("number")
    if type(candidate) is int and 0 <= candidate <= U64_MAX:
        return candidate
    raise SlurmAutoError(f"malformed {field}: expected a finite, nonnegative integer, got {value!r}")


def parse_partitions(value: object) -> Tuple[str, ...]:
    if value is None or value in ("", "(null)"):
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    elif isinstance(value, list):
        raise SlurmAutoError("malformed partition membership: expected strings")
    else:
        raise SlurmAutoError("malformed partitions: expected an array of names or comma-separated string")

    names = set()
    for item in items:
        name = item.strip(WHITESPACE)
        if not name or re.search(r"[\x00-\x1f\x7f-\x9f]", name):
            raise SlurmAutoError("malformed partition membership: empty name or control character")
        names.add(name)
    return tuple(sorted(names))


def parse_gres(text: str) -> int:
    """Count configured GPU GRES, including entries with comma-separated annotations."""
    text = text.strip(WHITESPACE)
    if not text or text == "(null)":
        return 0
    invalid = SlurmAutoError(
        f"cannot reliably interpret configured GRES {text!r}; "
        "expected gpu[:type]:count with optional metadata annotations"
    )

    entries: List[str] = []
    start = 0
    inside = False
    for index, character in enumerate(text):
        if character == "(" and not inside:
            inside = True
        elif character == ")" and inside:
            inside = False
        elif character in "()":
            raise invalid
        elif character == "," and not inside:
            entries.append(text[start:index])
            start = index + 1
    if inside:
        raise invalid
    entries.append(text[start:])

    total = 0
    for entry in entries:
        base, separator, annotations = entry.strip(WHITESPACE).partition("(")
        if separator and not GRES_ANNOTATIONS.fullmatch(separator + annotations):
            raise invalid
        parts = base.strip(WHITESPACE).split(":")
        if len(parts) < 2 or any(not part or any(c in WHITESPACE for c in part) for part in parts):
            raise invalid
        if parts[0] != "gpu":
            continue
        if len(parts) not in (2, 3):
            raise invalid
        try:
            total += unsigned_count(parts[-1])
        except ValueError as error:
            raise invalid from error
        if total > U64_MAX:
            raise invalid
    return total
