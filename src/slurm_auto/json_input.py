import json
from typing import Any, Dict, List, Tuple


def reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field {key!r}")
        result[key] = value
    return result


def load(source: str) -> Any:
    value = json.loads(source, parse_constant=reject_constant, object_pairs_hook=unique_object)
    # JSON escapes can produce lone surrogates even when the source bytes were UTF-8.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
    return value
