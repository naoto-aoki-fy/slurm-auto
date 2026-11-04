"""Validate each partition independently, preserving differing node counts."""

from typing import Tuple

from .models import NodeResources


def validate_resources(nodes: Tuple[NodeResources, ...]) -> Tuple[str, ...]:
    errors = []
    for attribute, label in (("cpus", "CPU"), ("gpus", "GPU")):
        groups = {}
        for node in nodes:
            groups.setdefault(getattr(node, attribute), []).append(node.name)
        if len(groups) > 1:
            detail = "; ".join(
                f"{count}: {', '.join(sorted(names))}"
                for count, names in sorted(groups.items())
            )
            errors.append(f"different {label} counts per node ({detail})")
    return tuple(errors)
